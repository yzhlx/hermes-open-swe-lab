"""GitHub event routing (D3 requirement A).

Routes inbound webhook events to task creation with strict allowlist + safe
ignore of unrelated events + task idempotency (one job per issue). Reuses the
HMAC verification and delivery dedup from :mod:`webhook_receiver` so the
security gates stay in exactly one place.

Routed events:
- ``issues`` (action opened/reopened)  -> create/return the issue's task
- ``issue_comment`` (created)          -> append a follow-up to the issue's task
- ``pull_request`` / ``check_run`` / ``status`` -> acknowledged, not actioned
  here (the scheduler drives PR/CI lifecycle; these are observed safely)

Any event for a non-allowed repo is rejected (``repo_not_allowed``). Any
unrouted event type is safely ignored (``ignored``).
"""
from __future__ import annotations

import json
from typing import Optional

from .control_plane import ControlPlane, ControlPlaneError
from .webhook_receiver import verify_signature
from .constants import ALLOWED_GITHUB_REPOS, ROLE_CODING_AGENT


class EventRouter:
    ROUTED_EVENTS = {"issues", "issue_comment", "pull_request", "check_run", "status"}

    def __init__(self, db_path: str, secret: str,
                 allowed_repos: Optional[set] = None, require_tls: bool = False,
                 allowed_token_hashes=None, replay_window: int = 300):
        self.db_path = db_path
        self.secret = secret
        self.allowed_repos = set(allowed_repos) if allowed_repos else set(ALLOWED_GITHUB_REPOS)
        self.require_tls = require_tls
        self.allowed_token_hashes = allowed_token_hashes
        self.replay_window = replay_window

    def handle(self, *, delivery_id: str, signature: str, event: str,
               raw_body: bytes, forwarded_proto: Optional[str] = None) -> dict:
        if self.require_tls and forwarded_proto != "https":
            raise ControlPlaneError("tls_required")
        if not verify_signature(self.secret, raw_body, signature):
            raise ControlPlaneError("bad_signature")

        cp = ControlPlane(self.db_path,
                          allowed_token_hashes=self.allowed_token_hashes,
                          replay_window=self.replay_window)
        try:
            # Delivery-id dedup (GitHub X-GitHub-Delivery) — D3 requirement #3.
            if not cp.record_delivery(delivery_id):
                return {"deduped": True, "delivery_id": delivery_id}

            body = json.loads(raw_body or b"{}")
            repo = body.get("repository", {}).get("full_name", "")
            if repo not in self.allowed_repos:
                raise ControlPlaneError("repo_not_allowed")

            if event not in self.ROUTED_EVENTS:
                return {"ignored": True, "event": event}

            handler = getattr(self, f"_on_{event}", None)
            if handler is None:
                return {"ignored": True, "event": event}
            return handler(cp, body, repo)
        finally:
            try:
                cp.conn.close()
            except Exception:
                pass

    # ---- event handlers --------------------------------------------------
    def _on_issues(self, cp: ControlPlane, body: dict, repo: str) -> dict:
        action = body.get("action")
        num = body.get("issue", {}).get("number")
        if action not in ("opened", "reopened"):
            return {"ignored": True, "action": action}
        jid, created = cp.create_issue_task(
            repo, num,
            {"event": "issues", "action": action, "payload": body},
            role=ROLE_CODING_AGENT)
        return {"ok": True, "created": created, "job_id": jid, "issue": num}

    def _on_issue_comment(self, cp: ControlPlane, body: dict, repo: str) -> dict:
        issue_num = body.get("issue", {}).get("number")
        comment_id = body.get("comment", {}).get("id")
        if not issue_num:
            return {"ignored": True, "reason": "no_issue"}
        jid = cp.get_job_by_issue(repo, issue_num)
        if jid is None:
            # No task yet for this issue — a follow-up with no parent is ignored.
            return {"ignored": True, "reason": "no_task_for_issue"}
        # Append the follow-up to the existing task (idempotent by comment id).
        cp.append_event(jid, {"id": f"comment-{comment_id}",
                              "type": "follow_up",
                              "payload": {"comment_id": comment_id}})
        return {"ok": True, "appended": True, "job_id": jid}

    def _on_pull_request(self, cp: ControlPlane, body: dict, repo: str) -> dict:
        return {"ignored": True, "event": "pull_request"}

    def _on_check_run(self, cp: ControlPlane, body: dict, repo: str) -> dict:
        return {"ignored": True, "event": "check_run"}

    _on_status = _on_check_run
