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
import urllib.request
import urllib.error

from .echo_sandbox import EchoSandboxBackend


class HermesWorker:
    def __init__(self, base_url: str, token: str, backend=None,
                 poll_interval: float = 2.0, max_events_batch: int = 50):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.backend = backend or EchoSandboxBackend()
        self.poll_interval = poll_interval
        self.max_events_batch = max_events_batch
        self._registered = False

    def _post(self, path, body=None):
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json",
                     "X-Worker-Token": self.token},
            method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=30)
            return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def register(self):
        self._post("/worker/register",
                   {"name": "hermes-local-worker",
                    "capabilities": ["echo-sandbox"]})
        self._registered = True

    def _run_job(self, job_id, payload):
        evs = []
        ws = self.backend.create()
        evs.append({"id": f"{job_id}-create", "type": "sandbox_create",
                    "payload": {"workspace": ws}})
        cmd = payload.get("command", "echo hello")
        res = self.backend.execute(cmd)
        evs.append({"id": f"{job_id}-exec", "type": "execute",
                    "payload": {"command": cmd, "exit_code": res.exit_code,
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
            "command": cmd,
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
