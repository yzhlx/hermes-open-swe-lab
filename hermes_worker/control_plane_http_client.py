"""Value-safe HTTPS adapter for the remote Hermes ControlPlane."""
from __future__ import annotations

import hmac
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Optional

from .control_plane import ControlPlaneError


_SAFE_REMOTE_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_HOST_EVENT_TYPES = {"pr_create_started"}


class ControlPlaneHttpClient:
    """Expose the ControlPlane subset required by ``HostAgentJobRunner``.

    The worker token is retained in process memory only. Every request carries a
    fresh nonce/timestamp and errors expose only stable error codes, never
    response bodies or credential-bearing request details.
    """

    def __init__(
        self,
        base_url: str,
        worker_token: str,
        *,
        timeout: float = 30.0,
        insecure_local_ok: bool = False,
        nonce_factory: Optional[Callable[[], str]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        normalized = base_url.rstrip("/")
        if normalized.startswith("http://") and not (
            insecure_local_ok
            and (
                normalized.startswith("http://127.0.0.1")
                or normalized.startswith("http://localhost")
                or normalized.startswith("http://[::1]")
            )
        ):
            raise ValueError("https_required")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("invalid_control_plane_url")
        if not worker_token:
            raise ValueError("worker_token_required")
        self.base_url = normalized
        self._worker_token = worker_token
        self.timeout = float(timeout)
        self.insecure_local_ok = bool(insecure_local_ok)
        self._nonce_factory = nonce_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or time.time

    def fork_for_thread(self) -> "ControlPlaneHttpClient":
        return ControlPlaneHttpClient(
            self.base_url,
            self._worker_token,
            timeout=self.timeout,
            insecure_local_ok=self.insecure_local_ok,
            nonce_factory=self._nonce_factory,
            clock=self._clock,
        )

    def close(self) -> None:
        return None

    def owns_worker_token(self, worker_token: str) -> bool:
        return bool(worker_token) and hmac.compare_digest(
            self._worker_token,
            worker_token,
        )

    def _assert_worker_token(self, worker_token: str) -> None:
        if not self.owns_worker_token(worker_token):
            raise ControlPlaneError("worker_identity_mismatch")

    @staticmethod
    def _error_code(raw: bytes, status: int) -> str:
        try:
            value = json.loads(raw.decode("utf-8"))
            candidate = value.get("error") if isinstance(value, dict) else None
        except (UnicodeDecodeError, ValueError):
            candidate = None
        if isinstance(candidate, str) and _SAFE_REMOTE_ERROR.fullmatch(candidate):
            return candidate
        return f"remote_http_{int(status)}"

    def _post(self, path: str, body: Optional[dict] = None) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(
                body or {},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Worker-Token": self._worker_token,
                "X-Nonce": self._nonce_factory(),
                "X-Timestamp": str(int(self._clock())),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise ControlPlaneError(
                self._error_code(raw, exc.code)
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ControlPlaneError("remote_control_plane_unavailable") from None
        if status < 200 or status >= 300:
            raise ControlPlaneError(f"remote_http_{int(status)}")
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            raise ControlPlaneError("remote_invalid_json") from None
        if not isinstance(value, dict):
            raise ControlPlaneError("remote_invalid_response")
        return value

    def register(self, name: str = "host-pi-worker", capabilities=None) -> dict:
        return self._post("/worker/register", {
            "name": name,
            "capabilities": capabilities or [
                "host-pi",
                "contained-workspace-tools",
                "docker-sandbox",
                "draft-pr",
            ],
        })

    def heartbeat(self, worker_token: str, job_id: Optional[int] = None) -> dict:
        self._assert_worker_token(worker_token)
        return self._post("/worker/heartbeat", {"job_id": job_id})

    def claim_job(self, worker_token: str, job_id: int) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/claim")

    def keepalive(self, worker_token: str, job_id: int) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/keepalive")

    def _snapshot(self, job_id: int) -> dict:
        return self._post(f"/worker/jobs/{int(job_id)}/snapshot")

    def get_job(self, job_id: int) -> dict:
        value = self._snapshot(job_id).get("job")
        if not isinstance(value, dict):
            raise ControlPlaneError("remote_invalid_job")
        return value

    def get_events(self, job_id: int) -> list:
        value = self._snapshot(job_id).get("events")
        if not isinstance(value, list):
            raise ControlPlaneError("remote_invalid_events")
        return value

    def transition_job(
        self,
        worker_token: str,
        job_id: int,
        state: str,
        payload: Optional[dict] = None,
    ) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/transition", {
            "state": state,
            "payload": payload or {},
        })

    def set_state(self, job_id: int, state: str) -> None:
        self.transition_job(self._worker_token, job_id, state)

    def append_event(self, job_id: int, event: dict) -> None:
        route = "host-events" if event.get("type") in _HOST_EVENT_TYPES else "events"
        self._post(f"/worker/jobs/{int(job_id)}/{route}", {
            "events": [event],
        })

    def store_agent_result(self, job_id: int, result: dict) -> None:
        self._post(f"/worker/jobs/{int(job_id)}/stage-result", {
            "result": result,
        })

    def finish_state(
        self,
        worker_token: str,
        job_id: int,
        state: str,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/finish", {
            "state": state,
            "result": result or {},
            "error": error,
        })

    def complete(
        self,
        worker_token: str,
        job_id: int,
        result: Optional[dict] = None,
    ) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/complete", {
            "result": result or {},
        })

    def handoff_for_review(
        self,
        worker_token: str,
        job_id: int,
        *,
        event: dict,
        result: dict,
    ) -> dict:
        self._assert_worker_token(worker_token)
        return self._post(f"/worker/jobs/{int(job_id)}/handoff", {
            "event": event,
            "result": result,
        })

    def request_installation_token(self, job_id: int) -> str:
        value = self._post(f"/internal/task/{int(job_id)}/token")
        token = value.get("token")
        if not isinstance(token, str) or not token:
            raise ControlPlaneError("installation_token_unavailable")
        return token
