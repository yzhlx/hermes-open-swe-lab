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
import urllib.request
import urllib.error

from .echo_sandbox import EchoSandboxBackend
from .redact import redact


class HermesWorker:
    def __init__(self, base_url: str, token: str, backend=None,
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
        self.backend = backend or EchoSandboxBackend()
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
            cmd = payload.get("command", "echo hello")
            res = self.backend.execute(cmd)
            # Command is redacted before it ever reaches logs/DB (item 5).
            evs.append({"id": f"{job_id}-exec", "type": "execute",
                        "payload": {"command": redact(cmd),
                                    "exit_code": res.exit_code,
                                    "stdout": res.stdout[:200]}})
            self.backend.write_file("automation-smoke-test/README.md",
                                    "# Smoke test\n")
            evs.append({"id": f"{job_id}-write", "type": "write_file",
                        "payload": {"path": "automation-smoke-test/README.md"}})
            # stream events (batched)
            for i in range(0, len(evs), self.max_events_batch):
                self._post(f"/worker/jobs/{job_id}/events",
                           {"events": evs[i:i + self.max_events_batch]})
            result = {
                "exit_code": res.exit_code,
                "command": redact(cmd),
                "container_id": "echo-" + str(job_id),
                "modified_files": ["automation-smoke-test/README.md"],
                "commit_sha": "simulated",
                "ci_status": "pending",
                "token_usage": 0,
                "tool_calls": len(evs),
                "model": payload.get("model", "echo-model"),
                "role": payload.get("role", "coding_agent"),
            }
            self._post(f"/worker/jobs/{job_id}/complete", {"result": result})
            self.backend.delete()
        finally:
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
