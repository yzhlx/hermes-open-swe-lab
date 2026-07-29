"""Cloud control-plane logic: task queue, lease, events, worker registry.

All mutating methods are idempotent where it matters:
- ``register`` is idempotent (same token -> same worker id).
- ``claim`` returns the already-owned job instead of double-claiming.
- ``complete``/``fail`` are no-ops once a job is already finished.
- ``post_events`` deduplicates by client-supplied ``event_id``.

No secrets are stored: only a sha256 hash of the worker token is kept for
lease-ownership checks.
"""
from __future__ import annotations

import json
import hashlib
import re
import time
import sqlite3
import functools
from typing import Optional

from .db import init_db, hash_token
from .constants import (
    HOST_WORKER_ACTIVE_STATES,
    HOST_WORKER_TERMINAL_STATES,
    ROLE_SCHEDULER,
)
from .redact import redact


TRUSTED_EVENT_CHANNELS = {
    "task_created": "control-room",
    "pause_requested": "control-room",
    "paused": "control-room",
    "resumed": "control-room",
    "plan_created": "planning",
    "operator_requirements_added": "planning",
    "progress": "implementation",
    "agent_run": "implementation",
    "review": "review",
    "head_mismatch": "review",
    "ci_passed": "qa",
    "ci_fail": "qa",
    "pr_created": "release",
    "draft_pr": "release",
    "push": "release",
    "round2_push": "release",
    "round2_label": "release",
    "await_user": "user-action-required",
    "operator_approved": "user-action-required",
    "operator_rejected": "user-action-required",
    "escalated": "user-action-required",
}
_RESERVED_EVENT_PAYLOAD_KEYS = {
    "source_type",
    "source_id",
    "actor_role",
    "role",
    "channel",
}
_WORKER_RESERVED_EVENT_TYPES = (
    set(TRUSTED_EVENT_CHANNELS)
    - {"plan_created", "progress", "agent_run"}
    | {"pr_create_started"}
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)


def _redact_persisted_value(value):
    """Recursively scrub credential-shaped text before durable storage."""
    if isinstance(value, str):
        clean = redact(value)
        return _PRIVATE_KEY_BLOCK_RE.sub(
            "[REDACTED PRIVATE KEY BLOCK]",
            clean,
        )
    if isinstance(value, dict):
        return {
            key: _redact_persisted_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_persisted_value(item) for item in value]
    return value


def trusted_event_channel(event_type: str) -> str:
    if event_type in TRUSTED_EVENT_CHANNELS:
        return TRUSTED_EVENT_CHANNELS[event_type]
    if isinstance(event_type, str) and event_type.startswith("ci_"):
        return "qa"
    return "control-room"


def trusted_event_type(event_type: str) -> bool:
    return (
        event_type in TRUSTED_EVENT_CHANNELS
        or isinstance(event_type, str)
        and event_type.startswith("ci_")
    )


def is_trusted_scheduler_event(
    event: dict,
    event_type: str,
    actor_role: str,
) -> bool:
    """Return whether an event carries server-derived Scheduler provenance."""
    return (
        event.get("event_type") == event_type
        and event.get("source_type") == "scheduler"
        and event.get("source_id") == ROLE_SCHEDULER
        and event.get("actor_role") == actor_role
        and event.get("display_trust") == "trusted"
    )


def _sanitize_event_payload(payload):
    if not isinstance(payload, dict):
        return _redact_persisted_value(payload)
    return _redact_persisted_value({
        key: value
        for key, value in payload.items()
        if key not in _RESERVED_EVENT_PAYLOAD_KEYS
    })


def _request_fingerprint(value) -> str:
    canonical = json.dumps(
        _redact_persisted_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ControlPlaneError(Exception):
    pass


def _retry_on_locked(func):
    """Retry a mutating method when SQLite raises a transient 'database is locked'.

    Under short-lived multi-writer contention (e.g. the concurrency test, or a
    control plane servicing several workers) a write can still surface a lock
    error despite ``busy_timeout``. Retrying keeps the operation correct instead
    of crashing the calling worker. Permanent errors (constraint violations,
    bad SQL) are re-raised immediately.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        last = None
        for _ in range(300):
            try:
                return func(self, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower():
                    last = exc
                    time.sleep(0.005)
                    continue
                raise
        raise last
    return wrapper


class ControlPlane:
    def __init__(self, db_path: str, now: Optional[callable] = None,
                 lease_seconds: int = 1200,
                 allowed_token_hashes: Optional[set] = None,
                 replay_window: int = 300):
        self.db_path = db_path
        self._now = now or time.time
        self.lease_seconds = lease_seconds
        # Server-side worker allowlist (D3 hardening, item 1). When set (non-
        # empty), only these token hashes may register. ``None`` = allow all
        # (local/offline test convenience only; production MUST pass it).
        self.allowed_token_hashes = set(allowed_token_hashes) if allowed_token_hashes else None
        # Replay-protection window in seconds (D3 hardening, item 3).
        self.replay_window = replay_window
        self.conn = init_db(db_path)

    # ---------------- workers ----------------
    def register(self, token: str, name: str = None, capabilities: str = None) -> dict:
        h = hash_token(token)
        if self.allowed_token_hashes is not None and h not in self.allowed_token_hashes:
            raise ControlPlaneError("worker_not_allowlisted")
        row = self.conn.execute(
            "SELECT worker_id FROM workers WHERE token_hash=?", (h,)).fetchone()
        if row:
            wid = row["worker_id"]
            self.conn.execute(
                "UPDATE workers SET last_heartbeat=? WHERE token_hash=?", (self._now(), h))
        else:
            wid = f"wk_{h[:12]}"
            self.conn.execute(
                "INSERT INTO workers(token_hash, worker_id, name, capabilities, "
                "last_heartbeat, created_at) VALUES (?,?,?,?,?,?)",
                (h, wid, name, capabilities, self._now(), self._now()))
        self.conn.commit()
        return {"ok": True, "worker_id": wid}

    def check_replay(self, nonce: str, ts) -> None:
        """Enforce request freshness + nonce uniqueness (item 3).

        Raises ``ControlPlaneError`` for missing/stale/duplicate requests.
        A fresh nonce is recorded with a TTL = replay_window; expired nonces
        are purged on each call.
        """
        if not nonce:
            raise ControlPlaneError("missing_nonce")
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            raise ControlPlaneError("bad_timestamp")
        now = self._now()
        if abs(now - ts) > self.replay_window:
            raise ControlPlaneError("stale_request")
        self.conn.execute("DELETE FROM nonces WHERE expires < ?", (now,))
        try:
            self.conn.execute(
                "INSERT INTO nonces(nonce, expires) VALUES (?,?)",
                (nonce, now + self.replay_window))
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise ControlPlaneError("replay_detected")

    def _check_token(self, token: str) -> str:
        h = hash_token(token)
        row = self.conn.execute(
            "SELECT worker_id FROM workers WHERE token_hash=?", (h,)).fetchone()
        if not row:
            raise ControlPlaneError("unknown_worker_token")
        return h

    # ---------------- jobs ----------------
    def create_job(self, payload: dict, task_id=None, issue_number=None,
                   pr_number=None, role=None, model=None, repo=None) -> int:
        payload = _redact_persisted_value(payload)
        cur = self.conn.execute(
            """INSERT INTO jobs(task_id, repo, issue_number, pr_number, state, role, model,
               payload, retries, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, repo, issue_number, pr_number, "pending", role, model,
             json.dumps(payload), 0, self._now()))
        self.conn.commit()
        return cur.lastrowid

    def create_job_idempotent(self, payload: dict, task_id: str,
                              repo=None, role=None, model=None) -> tuple[int, bool]:
        """Create one durable job per deterministic task id.

        The SQLite write lock serializes the read-then-insert sequence across
        HTTP request connections, so request replay does not depend on process
        memory.
        """
        if not task_id:
            raise ControlPlaneError("task_id_required")
        payload = _redact_persisted_value(payload)
        request_fingerprint = _request_fingerprint({
            "task_id": task_id,
            "repo": repo,
            "role": role,
            "model": model,
            "payload": payload,
        })
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE task_id=? ORDER BY id ASC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row:
                existing = dict(row)
                stored_fingerprint = existing.get("request_fingerprint")
                if not stored_fingerprint:
                    try:
                        stored_payload = json.loads(
                            existing.get("payload") or "{}"
                        )
                    except (TypeError, ValueError):
                        raise ControlPlaneError("idempotency_conflict")
                    stored_fingerprint = _request_fingerprint({
                        "task_id": existing.get("task_id"),
                        "repo": existing.get("repo"),
                        "role": existing.get("role"),
                        "model": existing.get("model"),
                        "payload": stored_payload,
                    })
                    self.conn.execute(
                        "UPDATE jobs SET request_fingerprint=? "
                        "WHERE id=? AND request_fingerprint IS NULL",
                        (stored_fingerprint, existing["id"]),
                    )
                if stored_fingerprint != request_fingerprint:
                    raise ControlPlaneError("idempotency_conflict")
                self.conn.execute("COMMIT")
                return existing["id"], False
            cur = self.conn.execute(
                """INSERT INTO jobs(
                   task_id, repo, state, role, model, payload,
                   request_fingerprint, retries, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    repo,
                    "pending",
                    role,
                    model,
                    json.dumps(payload),
                    request_fingerprint,
                    0,
                    self._now(),
                ),
            )
            self.conn.execute("COMMIT")
            return cur.lastrowid, True
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def get_job_by_task_id(self, task_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE task_id=? ORDER BY id ASC LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def record_delivery(self, delivery_id: str, now=None) -> bool:
        """Record a webhook delivery id.

        Returns ``True`` if this is a new delivery (accept), ``False`` if it was
        seen before (duplicate → caller should treat as idempotent no-op). This
        is the GitHub ``X-GitHub-Delivery`` dedup store (D3 requirement #3).
        """
        try:
            self.conn.execute(
                "INSERT INTO deliveries(delivery_id, ts) VALUES (?,?)",
                (delivery_id, now or self._now()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_job_by_issue(self, repo: str, issue_number: int) -> Optional[int]:
        """Return the job id bound to ``(repo, issue_number)`` or ``None``.

        Used to enforce one-task-per-issue idempotency (D3 requirement #25).
        """
        row = self.conn.execute(
            "SELECT job_id FROM issue_tasks WHERE repo=? AND issue_number=?",
            (repo, issue_number)).fetchone()
        return row["job_id"] if row else None

    def get_job_by_pr(self, repo: str, pr_number: int) -> Optional[int]:
        """Return the job id for a known PR number, or ``None``."""
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE repo=? AND pr_number=?",
            (repo, pr_number)).fetchone()
        return row["id"] if row else None

    def create_issue_task(self, repo: str, issue_number: int, payload: dict,
                          role=None, model=None, task_id=None) -> tuple:
        """Idempotently bind one job to ``(repo, issue_number)``.

        Returns ``(job_id, created)``. If a task already exists for this issue,
        the existing ``job_id`` is returned with ``created=False`` (no duplicate
        task, no duplicate PR). Otherwise a fresh job + ``issue_tasks`` row is
        created atomically.
        """
        existing = self.get_job_by_issue(repo, issue_number)
        if existing is not None:
            return existing, False
        payload = _redact_persisted_value(payload)
        try:
            cur = self.conn.execute(
                """INSERT INTO jobs(repo, issue_number, state, role, model,
                   payload, retries, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (repo, issue_number, "pending", role, model,
                 json.dumps(payload), 0, self._now()))
            jid = cur.lastrowid
            self.conn.execute(
                "INSERT INTO issue_tasks(repo, issue_number, job_id, created_at) "
                "VALUES (?,?,?,?)",
                (repo, issue_number, jid, self._now()))
            self.conn.commit()
            return jid, True
        except sqlite3.IntegrityError:
            # Race: another writer inserted the issue_tasks row first.
            self.conn.rollback()
            return self.get_job_by_issue(repo, issue_number), False

    def update_job(self, job_id: int, **fields) -> None:
        """Update arbitrary scalar columns on a job (pr_number, round, ci_status, ...)."""
        if not fields:
            return
        set_cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {set_cols} WHERE id=?",
            list(fields.values()) + [job_id])
        self.conn.commit()

    def record_round2_signal(self, job_id: int, pr_number: int) -> None:
        """Atomically persist the scheduler's round-2 authorization."""
        event_id = f"round2-label:{job_id}:{pr_number}:2"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "UPDATE jobs SET round=2 WHERE id=?",
                (job_id,),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO events("
                "job_id, event_type, seq, event_id, ts, payload, "
                "source_type, source_id, actor_role, channel, display_trust) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    "round2_label",
                    0,
                    event_id,
                    self._now(),
                    json.dumps({"pr_number": pr_number}),
                    "scheduler",
                    "scheduler",
                    "scheduler",
                    trusted_event_channel("round2_label"),
                    "trusted",
                ),
            )
            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def set_state(self, job_id: int, state: str) -> None:
        """Move a job to an explicit state (agent_done / in_review / await_user / escalated)."""
        if isinstance(state, str) and state.upper() == "FINAL_ACCEPTANCE":
            raise ControlPlaneError("final_acceptance_requires_evidence")
        self.conn.execute("UPDATE jobs SET state=? WHERE id=?", (state, job_id))
        self.conn.commit()

    def apply_operator_action(
        self,
        job_id: int,
        action: str,
        *,
        request_id: str = None,
        expected_version: int = None,
        reason: str = None,
        requirements=None,
    ) -> dict:
        """Apply one durable, versioned operator command.

        Request-id replay is resolved before optimistic-version validation.
        Forbidden commands never create an event, action record, or job update.
        """
        if request_id is None:
            # Backward-compatible deterministic key for the original pause /
            # resume contract. New HTTP callers always supply a request id.
            request_id = f"legacy:{job_id}:{action}"
        if not isinstance(request_id, str) or not request_id.strip():
            raise ControlPlaneError("request_id_required")
        request_id = request_id.strip()
        request_key = "sha256:" + hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest()
        display_request_id = _redact_persisted_value(request_id)
        reason = _redact_persisted_value(reason)
        requirements = _redact_persisted_value(requirements)
        request_fingerprint = _request_fingerprint({
            "action": action,
            "reason": reason,
            "requirements": requirements,
        })

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            replay = self.conn.execute(
                "SELECT action, request_fingerprint, result "
                "FROM operator_actions "
                "WHERE job_id=? AND request_id=?",
                (job_id, request_key),
            ).fetchone()
            if replay:
                stored_fingerprint = replay["request_fingerprint"]
                if (
                    stored_fingerprint
                    and stored_fingerprint != request_fingerprint
                ) or (
                    not stored_fingerprint
                    and replay["action"] != action
                ):
                    raise ControlPlaneError("idempotency_conflict")
                result = json.loads(replay["result"])
                self.conn.execute("COMMIT")
                return result

            if action in {"merge", "auto_merge", "complete"}:
                raise ControlPlaneError("operator_action_not_allowed")
            if action not in {
                "pause", "resume", "supplement", "reject", "approve"
            }:
                raise ControlPlaneError("operator_action_not_allowed")

            job = self._get_job(job_id)
            version = job.get("version") or 0
            if expected_version is not None and expected_version != version:
                raise ControlPlaneError("version_conflict")

            current_control = job.get("control_state") or "active"
            state = job["state"]
            next_state = state
            next_control = current_control
            next_requirements = job.get("requirements")
            requirements_revision = job.get("requirements_revision") or 0
            operator_events = []

            if action == "pause":
                if state != "pending":
                    raise ControlPlaneError(
                        "operator_action_requires_pending_job"
                    )
                next_control = "paused"
                if current_control != "paused":
                    operator_events = ["pause_requested", "paused"]
            elif action == "resume":
                if state != "pending":
                    raise ControlPlaneError(
                        "operator_action_requires_pending_job"
                    )
                next_control = "active"
                if current_control != "active":
                    operator_events = ["resumed"]
            elif action == "supplement":
                empty_requirements = requirements is None
                if isinstance(requirements, str):
                    empty_requirements = not requirements.strip()
                elif hasattr(requirements, "__len__"):
                    empty_requirements = len(requirements) == 0
                if empty_requirements:
                    raise ControlPlaneError("requirements_required")
                requirements_revision += 1
                next_requirements = json.dumps(
                    requirements, ensure_ascii=False, separators=(",", ":")
                )
                operator_events = ["operator_requirements_added"]
            elif action == "reject":
                if state not in {"await_user", "ready_for_manual_merge"}:
                    raise ControlPlaneError("reject_not_allowed_in_state")
                next_state = "agent_done"
                operator_events = ["operator_rejected"]
            elif action == "approve":
                if state != "await_user":
                    raise ControlPlaneError("approve_not_allowed_in_state")
                next_state = "ready_for_manual_merge"
                operator_events = ["operator_approved"]

            next_version = version + 1
            self.conn.execute(
                "UPDATE jobs SET state=?, control_state=?, version=?, "
                "requirements=?, requirements_revision=? WHERE id=?",
                (
                    next_state,
                    next_control,
                    next_version,
                    next_requirements,
                    requirements_revision,
                    job_id,
                ),
            )

            now = self._now()
            request_fragment = request_key.removeprefix("sha256:")[:24]
            event_payload = {
                "action": action,
                "request_id": display_request_id,
                "version": next_version,
            }
            if reason is not None:
                event_payload["reason"] = reason
            if action == "supplement":
                event_payload["requirements"] = requirements
                event_payload["requirements_revision"] = requirements_revision
            for event_type in operator_events:
                self.conn.execute(
                    "INSERT INTO events("
                    "job_id, event_type, seq, event_id, ts, payload, "
                    "source_type, source_id, actor_role, channel, display_trust) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        event_type,
                        0,
                        f"operator:{job_id}:{request_fragment}:{event_type}",
                        now,
                        json.dumps(
                            event_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "operator",
                        "human_owner",
                        "human_owner",
                        trusted_event_channel(event_type),
                        "trusted" if trusted_event_type(event_type)
                        else "unverified",
                    ),
                )

            result = {
                "ok": True,
                "action": action,
                "request_id": display_request_id,
                "state": next_state,
                "control_state": next_control,
                "version": next_version,
                "requirements_revision": requirements_revision,
            }
            self.conn.execute(
                "INSERT INTO operator_actions("
                "job_id, request_id, action, request_fingerprint, "
                "result, created_at) VALUES (?,?,?,?,?,?)",
                (
                    job_id,
                    request_key,
                    action,
                    request_fingerprint,
                    json.dumps(result, separators=(",", ":")),
                    now,
                ),
            )
            self.conn.execute("COMMIT")
            return result
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def store_agent_result(self, job_id: int, result: dict) -> None:
        """Record agent output fields without finalizing the job (allows rework).

        List/dict fields (``modified_files``, ``token_usage``) are JSON-encoded
        because the ``jobs`` columns are TEXT.
        """
        upd = {}
        for k in ("modified_files", "token_usage", "tool_calls", "container_id",
                  "command", "commit_sha", "ci_status", "model", "role", "pr_number",
                  "round", "exit_code", "error"):
            if k in result and result[k] is not None:
                v = _redact_persisted_value(result[k])
                if k in ("modified_files", "token_usage") and not isinstance(v, str):
                    v = json.dumps(v)
                upd[k] = v
        if upd:
            self.update_job(job_id, **upd)

    def heartbeat(self, token: str, job_id: Optional[int] = None) -> dict:
        h = self._check_token(token)
        now = self._now()
        self.conn.execute(
            "UPDATE workers SET last_heartbeat=? WHERE token_hash=?", (now, h))
        reaped = self.reap_expired_leases(now=now)
        self.conn.commit()
        return {"ok": True, "reaped": reaped}

    def keepalive(self, token: str, job_id: int) -> dict:
        """Extend the lease of an owned, active job (item 4: mid-job keepalive).

        Long-running agent steps call this periodically so the lease does not
        expire mid-job and the job is not reaped/re-queued underneath them.
        """
        h = self._check_token(token)
        job = self._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        if job["state"] not in ("claimed", *HOST_WORKER_ACTIVE_STATES):
            raise ControlPlaneError("job_not_active")
        self.conn.execute(
            "UPDATE jobs SET lease_expires=? WHERE id=?",
            (self._now() + self.lease_seconds, job_id))
        self.conn.commit()
        return {"ok": True, "lease_expires": self._now() + self.lease_seconds}

    @_retry_on_locked
    def reap_expired_leases(self, now: Optional[float] = None) -> int:
        now = now if now is not None else self._now()
        # Read-first: only take a write lock when there is actually something to
        # reap. On every claim (which calls reap), fresh leases mean nothing is
        # expired -> the hot path stays a read and never contends on the write
        # lock. This is what keeps concurrent claim() fast under WAL.
        active = ("claimed", *HOST_WORKER_ACTIVE_STATES)
        placeholders = ",".join("?" for _ in active)
        probe = self.conn.execute(
            f"SELECT 1 FROM jobs WHERE state IN ({placeholders}) "
            "AND lease_expires IS NOT NULL AND lease_expires < ? LIMIT 1",
            (*active, now)).fetchone()
        if not probe:
            return 0
        cur = self.conn.execute(
            f"""UPDATE jobs SET state='pending', worker_token_hash=NULL, lease_expires=NULL
                WHERE state IN ({placeholders}) AND lease_expires IS NOT NULL
                AND lease_expires < ?""",
            (*active, now))
        self.conn.commit()
        return cur.rowcount

    @_retry_on_locked
    def claim(self, token: str) -> dict:
        h = self._check_token(token)
        self.reap_expired_leases()
        # 1) Re-claim an already-owned (claimed/running/agent_done) job — idempotent.
        #    'agent_done' is re-claimable so a round-2 rework can pick the same
        #    task back up after the scheduler signals rework.
        owned_states = ("claimed", "agent_done", *HOST_WORKER_ACTIVE_STATES)
        placeholders = ",".join("?" for _ in owned_states)
        cur = self.conn.execute(
            f"SELECT id FROM jobs WHERE state IN ({placeholders}) "
            "AND worker_token_hash=? "
            "AND COALESCE(control_state, 'active')='active' LIMIT 1",
            (*owned_states, h))
        row = cur.fetchone()
        if row:
            job = self._get_job(row["id"])
            if job["state"] != "running":
                self.conn.execute(
                    "UPDATE jobs SET state='running', started_at=COALESCE(started_at,?) "
                    "WHERE id=?", (self._now(), row["id"]))
                self.conn.commit()
            return {"job_id": job["id"],
                    "payload": json.loads(job["payload"] or "{}"),
                    "already_claimed": True}
        # 2) Atomic claim of a pending (or agent_done, for rework) job (item 6).
        #    A single UPDATE...RETURNING is serialized by SQLite's write lock,
        #    so two concurrent workers can never grab the same pending job.
        lease = self._now() + self.lease_seconds
        cur = self.conn.execute(
            """UPDATE jobs SET state='running', worker_token_hash=?, lease_expires=?, started_at=?
               WHERE id = (SELECT id FROM jobs WHERE state IN ('pending','agent_done')
                            AND COALESCE(control_state, 'active')='active'
                            ORDER BY created_at ASC LIMIT 1)
               RETURNING id""",
            (h, lease, self._now()))
        row = cur.fetchone()
        if not row:
            return {"empty": True}
        self.conn.commit()
        jid = row["id"]
        job = self._get_job(jid)
        return {"job_id": jid, "payload": json.loads(job["payload"] or "{}"),
                "lease_expires": lease}

    @_retry_on_locked
    def claim_job(self, token: str, job_id: int) -> dict:
        """Claim one explicitly selected job without consuming another pending job."""
        h = self._check_token(token)
        self.reap_expired_leases()
        job = self._get_job(job_id)
        if (job.get("control_state") or "active") != "active":
            raise ControlPlaneError("job_paused")
        if job["worker_token_hash"] == h and job["state"] in (
            "claimed", "agent_done", *HOST_WORKER_ACTIVE_STATES
        ):
            lease = self._now() + self.lease_seconds
            self.conn.execute(
                "UPDATE jobs SET state='running', lease_expires=?, "
                "started_at=COALESCE(started_at,?) WHERE id=?",
                (lease, self._now(), job_id),
            )
            self.conn.commit()
            return {"job_id": job_id, "already_claimed": True,
                    "lease_expires": lease}
        if job["state"] not in ("pending", "agent_done") or job["worker_token_hash"]:
            raise ControlPlaneError("job_not_claimable")
        lease = self._now() + self.lease_seconds
        cur = self.conn.execute(
            """UPDATE jobs
               SET state='running', worker_token_hash=?, lease_expires=?,
                   started_at=COALESCE(started_at,?)
               WHERE id=? AND state IN ('pending','agent_done')
                     AND worker_token_hash IS NULL""",
            (h, lease, self._now(), job_id),
        )
        if cur.rowcount != 1:
            raise ControlPlaneError("job_not_claimable")
        self.conn.commit()
        return {"job_id": job_id, "lease_expires": lease}

    def post_events(self, token: str, job_id: int, events: list) -> dict:
        h = self._check_token(token)
        job = self._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        worker = self.conn.execute(
            "SELECT worker_id FROM workers WHERE token_hash=?",
            (h,),
        ).fetchone()
        if not worker:
            raise ControlPlaneError("unknown_worker_token")
        worker_id = worker["worker_id"]
        actor_role = job.get("role") or "worker"
        for event in events:
            event_type = event.get("type")
            if (
                event_type in _WORKER_RESERVED_EVENT_TYPES
                or isinstance(event_type, str)
                and event_type.startswith("ci_")
            ):
                raise ControlPlaneError("worker_event_type_forbidden")
        seq = 0
        accepted = 0
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                etype = ev.get("type")
                eid = ev.get("id")
                payload = json.dumps(
                    _sanitize_event_payload(ev.get("payload", {}))
                )
                channel = trusted_event_channel(etype)
                display_trust = (
                    "trusted" if trusted_event_type(etype)
                    else "unverified"
                )
                try:
                    if eid:
                        self.conn.execute(
                            "INSERT INTO events("
                            "job_id, event_type, seq, event_id, ts, payload, "
                            "source_type, source_id, actor_role, channel, "
                            "display_trust) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                job_id,
                                etype,
                                seq,
                                eid,
                                self._now(),
                                payload,
                                "worker",
                                worker_id,
                                actor_role,
                                channel,
                                display_trust,
                            ),
                        )
                    else:
                        self.conn.execute(
                            "INSERT INTO events("
                            "job_id, event_type, seq, ts, payload, source_type, "
                            "source_id, actor_role, channel, display_trust) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                job_id,
                                etype,
                                seq,
                                self._now(),
                                payload,
                                "worker",
                                worker_id,
                                actor_role,
                                channel,
                                display_trust,
                            ),
                        )
                    accepted += 1
                except sqlite3.IntegrityError:
                    pass  # duplicate event_id -> idempotent no-op
                seq += 1
            self.conn.execute("COMMIT")
            return {"ok": True, "accepted": accepted}
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def append_event(self, job_id: int, event: dict) -> None:
        """Internal event append (no worker-auth) for routing follow-ups.

        Used by the event router to attach an ``issue_comment`` follow-up to an
        existing issue task. Idempotent by ``event["id"]`` (duplicate comment →
        no-op). Does NOT require a worker token (it is control-plane-internal).
        """
        job = self._get_job(job_id)
        etype = event.get("type")
        eid = event.get("id")
        payload = json.dumps(
            _sanitize_event_payload(event.get("payload", {}))
        )
        source_type = event.get("source_type") or "legacy"
        if source_type == "legacy":
            source_id = "unverified"
            actor_role = None
        elif source_type == "operator":
            source_id = "human_owner"
            actor_role = "human_owner"
        else:
            source_id = event.get("source_id") or "unverified"
            actor_role = event.get("actor_role")
        channel = trusted_event_channel(etype)
        display_trust = (
            "trusted"
            if source_type != "legacy" and trusted_event_type(etype)
            else "unverified"
        )
        try:
            self.conn.execute(
                "INSERT INTO events(job_id, event_type, seq, event_id, ts, payload, "
                "source_type, source_id, actor_role, channel, display_trust) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, etype, 0, eid, self._now(), payload,
                 source_type, source_id, actor_role, channel, display_trust))
        except sqlite3.IntegrityError:
            pass  # duplicate follow-up id -> idempotent no-op
        self.conn.commit()

    def _finish(self, token, job_id, final_state, updates: dict) -> dict:
        h = self._check_token(token)
        job = self._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        if job["ended_at"] is not None:
            return {"ok": True, "already_finished": True, "state": job["state"]}
        assignments = [
            "state=?",
            "ended_at=?",
            "lease_expires=NULL",
        ]
        params = [final_state, self._now()]
        for key, value in updates.items():
            clean_value = _redact_persisted_value(value)
            if isinstance(clean_value, (dict, list, tuple)):
                clean_value = json.dumps(
                    clean_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            assignments.append(f"{key}=?")
            params.append(clean_value)
        params.append(job_id)
        self.conn.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE id=?", params)
        self.conn.commit()
        return {"ok": True, "state": final_state}

    def finish_state(self, token: str, job_id: int, state: str,
                     result: dict = None, error: str = None) -> dict:
        """Finish a Host Agent job in an explicit terminal state and release its lease."""
        if state not in HOST_WORKER_TERMINAL_STATES:
            raise ControlPlaneError("invalid_terminal_state")
        updates = {}
        for key, value in (result or {}).items():
            if key not in {
                "modified_files", "container_id", "command", "exit_code",
                "commit_sha", "ci_status", "model", "role", "pr_number",
                "round", "token_usage", "tool_calls",
            } or value is None:
                continue
            if key in ("modified_files", "token_usage") and not isinstance(value, str):
                value = json.dumps(value)
            updates[key] = value
        if error:
            updates["error"] = error
        return self._finish(token, job_id, state, updates)

    def complete(self, token, job_id, result: dict = None) -> dict:
        upd = {}
        if result:
            mf = result.get("modified_files")
            if isinstance(mf, (list, dict)):
                mf = json.dumps(mf)
            upd["modified_files"] = mf
            for k in ("token_usage", "tool_calls", "container_id", "command",
                      "exit_code", "commit_sha", "ci_status", "model", "role"):
                if k in result and result[k] is not None:
                    upd[k] = result[k]
        return self._finish(token, job_id, "completed", upd)

    def fail(self, token, job_id, error: str = None) -> dict:
        upd = {"error": error} if error else {}
        return self._finish(token, job_id, "failed", upd)

    # ---------------- reads ----------------
    def _get_job(self, jid: int) -> dict:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise ControlPlaneError("job_not_found")
        return dict(row)

    def get_job(self, jid: int) -> dict:
        return self._get_job(jid)

    def get_events(self, jid: int) -> list:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY seq ASC, id ASC", (jid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_events_after(self, job_id: int, after_id: int, limit: int) -> list:
        """Return an event cursor page ordered by the durable event row id."""
        if limit <= 0:
            return []
        rows = self.conn.execute(
            "SELECT * FROM events WHERE job_id=? AND id>? "
            "ORDER BY id ASC LIMIT ?",
            (job_id, after_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_task_projection(self, job_id: int,
                            heartbeat_stale_after: float) -> dict:
        """Project workflow, operator control, and live connection separately."""
        job = self._get_job(job_id)
        last_heartbeat = None
        connection_state = "agent_not_started"
        worker_hash = job.get("worker_token_hash")
        if worker_hash:
            row = self.conn.execute(
                "SELECT last_heartbeat FROM workers WHERE token_hash=?",
                (worker_hash,),
            ).fetchone()
            last_heartbeat = row["last_heartbeat"] if row else None
            if last_heartbeat is None:
                connection_state = "disconnected"
            elif self._now() - last_heartbeat > heartbeat_stale_after:
                connection_state = "disconnected"
            else:
                connection_state = "online"
        elif job["state"] != "pending":
            connection_state = "disconnected"

        return {
            "state": job["state"],
            "control_state": job.get("control_state") or "active",
            "version": int(job.get("version") or 0),
            "requirements_revision": int(
                job.get("requirements_revision") or 0
            ),
            "connection_state": connection_state,
            "is_cached": False,
            "last_heartbeat_at": last_heartbeat,
        }

    def list_jobs(self, state: Optional[str] = None) -> list:
        if state:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE state=?", (state,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
