"""Local Hermes Worker — outbound HTTPS only.

Polls the cloud Worker API, claims one job at a time, runs it through a sandbox
backend, streams events, and reports completion/failure. It NEVER opens an
inbound port, exposes the Docker socket, or requires public SSH.

For D1 the backend is ``EchoSandboxBackend`` (offline protocol test). D2 swaps
in ``HermesDockerSandboxBackend``. The worker identity token is supplied by the
caller and stored only in a local restricted file (never in logs or the DB).
"""
from __future__ import annotations

import json
import time
import threading
import uuid
import subprocess
import urllib.request
import urllib.error

from .echo_sandbox import EchoSandboxBackend
from .docker_sandbox import HermesDockerSandboxBackend
from .agent_runner import AgentRunner, build_agent_runner
from .constants import HERMES_SANDBOX_BACKEND_ENV, DEFAULT_HERMES_SANDBOX_BACKEND
from .redact import redact


def _create_real_commit(workspace: str, message: str):
    """Create a REAL local git commit in ``workspace`` and return its SHA.

    PB-4 contract: a Coding Worker must never report the literal ``"simulated"``
    (or any placeholder) as a deliverable commit. If a real commit cannot be
    produced (no git, or nothing to commit), this returns ``None`` — never a
    fake. The Demo path therefore always yields a genuine, existing git SHA.
    """
    try:
        def _git(*args):
            return subprocess.run(
                ["git", "-C", workspace, *args],
                capture_output=True, text=True, timeout=60)

        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            if _git("init").returncode != 0:
                return None
        if _git("add", "-A").returncode != 0:
            return None
        # Nothing staged -> nothing to commit -> no fake SHA.
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return None
        r = _git("-c", "user.email=hermes-worker@local",
                 "-c", "user.name=hermes-local-worker",
                 "commit", "-m", message)
        if r.returncode != 0:
            return None
        out = _git("rev-parse", "HEAD")
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def build_sandbox_backend(backend: str | None = None):
    """Select the worker sandbox without ever falling back from Docker to host.

    ``echo`` is intentionally the default for offline tests.  Choosing
    ``docker`` creates the D2-validated isolated Docker backend; an invalid
    value is a configuration error rather than a permissive fallback.
    """
    import os

    selected = (backend or os.environ.get(
        HERMES_SANDBOX_BACKEND_ENV, DEFAULT_HERMES_SANDBOX_BACKEND)).strip().lower()
    if selected == "echo":
        return EchoSandboxBackend()
    if selected == "docker":
        return HermesDockerSandboxBackend()
    raise ValueError(
        f"unsupported {HERMES_SANDBOX_BACKEND_ENV}={selected!r}; expected echo or docker")


class _EventingSandbox:
    """Proxy agent sandbox operations into redacted worker lifecycle events."""

    def __init__(self, sandbox, job_id: int, events: list):
        self._sandbox = sandbox
        self._job_id = job_id
        self._events = events
        self.modified_files: list[str] = []
        self.exit_code = 0

    def _append(self, event_type: str, payload: dict) -> None:
        self._events.append({
            "id": f"{self._job_id}-{event_type}-{len(self._events)}",
            "type": event_type,
            "payload": payload,
        })

    def execute(self, command, *args, **kwargs):
        result = self._sandbox.execute(command, *args, **kwargs)
        self.exit_code = result.exit_code
        self._append("execute", {
            "command": redact(str(command)),
            "exit_code": result.exit_code,
            "stdout": redact(str(result.stdout))[:200],
        })
        return result

    def write_file(self, path, content):
        result = self._sandbox.write_file(path, content)
        self.modified_files.append(path)
        self._append("write_file", {"path": redact(str(path))})
        return result

    def __getattr__(self, name):
        return getattr(self._sandbox, name)


class HermesWorker:
    def __init__(self, base_url: str, token: str, backend=None,
                 agent_runner: AgentRunner | None = None, relay_client=None,
                 poll_interval: float = 2.0, max_events_batch: int = 50,
                 insecure_local_ok: bool = False,
                 keepalive_interval: float = 15.0):
        # Deployment hard gate (item 2): real deployments MUST use HTTPS.
        # The only exception is a local/offline test that passes
        # insecure_local_ok=True explicitly (never in production).
        if base_url.startswith("http://") and not insecure_local_ok:
            raise ValueError(
                "worker base_url must be https:// in real deployments; "
                "pass insecure_local_ok=True only for local/offline tests")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.backend = backend or build_sandbox_backend()
        self.agent_runner = agent_runner or build_agent_runner(relay_client=relay_client)
        self.poll_interval = poll_interval
        self.max_events_batch = max_events_batch
        self.insecure_local_ok = insecure_local_ok
        self.keepalive_interval = keepalive_interval
        self._registered = False

    def _post(self, path, body=None):
        data = json.dumps(body or {}).encode("utf-8")
        # Replay protection (item 3): unique nonce + current timestamp on every
        # request. The control plane rejects reused/stale nonces.
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json",
                     "X-Worker-Token": self.token,
                     "X-Nonce": str(uuid.uuid4()),
                     "X-Timestamp": str(int(time.time()))},
            method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=30)
            return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def register(self):
        self._post("/worker/register",
                   {"name": "hermes-local-worker",
                    "capabilities": ["docker-sandbox", "echo-sandbox"]})
        self._registered = True

    def _keepalive_loop(self, job_id, stop: threading.Event):
        while not stop.is_set():
            try:
                self._post(f"/worker/jobs/{job_id}/keepalive", {})
            except Exception:
                pass  # best-effort; next tick retries
            stop.wait(self.keepalive_interval)

    def _run_job(self, job_id, payload):
        # Mid-job lease keepalive (item 4): a background thread renews the lease
        # so long-running agent steps are not reaped underneath this worker.
        stop_ka = threading.Event()
        ka = threading.Thread(target=self._keepalive_loop,
                              args=(job_id, stop_ka), daemon=True)
        ka.start()
        try:
            evs = []
            ws = self.backend.create()
            evs.append({"id": f"{job_id}-create", "type": "sandbox_create",
                        "payload": {"workspace": ws}})
            instruction = payload.get("instruction") or payload.get("command", "")
            eventing_sandbox = _EventingSandbox(self.backend, job_id, evs)
            evidence = self.agent_runner.run(
                eventing_sandbox, ws, instruction,
                round=int(payload.get("round", 1)))
            # PB-4: preserve an actual agent-supplied SHA, otherwise create a
            # real local commit from the agent's edits (never a placeholder).
            commit_sha = evidence.commit_sha or _create_real_commit(
                ws, f"hermes worker job {job_id}")
            evs.append({"id": f"{job_id}-agent-complete", "type": "agent_complete",
                        "payload": {
                            "commit_sha": commit_sha,
                            "modified_files": evidence.modified_files or eventing_sandbox.modified_files,
                            "model": redact(str(evidence.model or "")),
                            "tool_calls": evidence.tool_calls,
                        }})
            # stream events (batched)
            for i in range(0, len(evs), self.max_events_batch):
                self._post(f"/worker/jobs/{job_id}/events",
                           {"events": evs[i:i + self.max_events_batch]})
            token_usage = evidence.token_usage
            # The legacy worker API persists a scalar token_usage column. Keep
            # the runner's detailed mapping in AgentEvidence while adapting it
            # to that established wire contract.
            if isinstance(token_usage, dict):
                token_usage = token_usage.get(
                    "total_tokens", token_usage.get("total", 0))
            result = {
                "exit_code": eventing_sandbox.exit_code,
                "command": redact(str(instruction)),
                "container_id": getattr(self.backend, "_container", None)
                                or "echo-" + str(job_id),
                "modified_files": evidence.modified_files or eventing_sandbox.modified_files,
                "commit_sha": commit_sha,
                "ci_status": "pending",
                "token_usage": token_usage,
                "tool_calls": evidence.tool_calls,
                "model": evidence.model or payload.get("model", "echo-model"),
                "role": payload.get("role", "coding_agent"),
            }
            self._post(f"/worker/jobs/{job_id}/complete", {"result": result})
        finally:
            try:
                self.backend.delete()
            except Exception:
                pass
            stop_ka.set()
            ka.join(timeout=2)

    def run_once(self):
        if not self._registered:
            self.register()
        self._post("/worker/heartbeat", {})
        code, resp = self._post("/worker/jobs/claim", {})
        if resp.get("empty"):
            return False
        jid = resp["job_id"]
        try:
            self._run_job(jid, resp.get("payload", {}))
        except Exception as e:  # noqa: BLE001 - report failure, never swallow
            self._post(f"/worker/jobs/{jid}/fail", {"error": str(e)})
        return True

    def loop(self, max_iterations=None):
        i = 0
        while True:
            if max_iterations is not None and i >= max_iterations:
                break
            did = self.run_once()
            i += 1
            if not did:
                if max_iterations is not None:
                    break
                time.sleep(self.poll_interval)
