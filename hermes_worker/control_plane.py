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
import time
import sqlite3
import functools
from typing import Optional

from .db import init_db, hash_token


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
                 replay_window: int = 300,
                 mode: str = "dev"):
        """Control-plane task queue / worker registry.

        ``mode`` selects the worker-token trust policy:

        * ``"dev"`` (default) — ``allowed_token_hashes=None`` is treated as
          allow-all. This preserves offline/test injectability and MUST NOT be
          used in production.
        * ``"production"`` — fail-closed. If ``allowed_token_hashes`` is ``None``
          or empty, construction raises ``ControlPlaneError`` so the server
          refuses to start instead of admitting any worker by default.

        The production entry point (``worker_api_server.main``) always passes
        ``mode="production"``.
        """
        self.db_path = db_path
        self._now = now or time.time
        self.lease_seconds = lease_seconds
        # Server-side worker allowlist (D3 hardening, item 1). When set (non-
        # empty), only these token hashes may register. ``None`` = allow all
        # (dev/test convenience only; never in production).
        self.allowed_token_hashes = set(allowed_token_hashes) if allowed_token_hashes else None
        self.mode = mode
        if mode == "production" and self.allowed_token_hashes is None:
            # Fail-closed: never start with an unconfigured worker allowlist.
            # The message carries no token value.
            raise ControlPlaneError("worker_tokens_not_configured_production")
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
        cur = self.conn.execute(
            """INSERT INTO jobs(task_id, repo, issue_number, pr_number, state, role, model,
               payload, retries, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, repo, issue_number, pr_number, "pending", role, model,
             json.dumps(payload), 0, self._now()))
        self.conn.commit()
        return cur.lastrowid

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

    def set_state(self, job_id: int, state: str) -> None:
        """Move a job to an explicit state (agent_done / in_review / await_user / escalated)."""
        self.conn.execute("UPDATE jobs SET state=? WHERE id=?", (state, job_id))
        self.conn.commit()

    def store_agent_result(self, job_id: int, result: dict) -> None:
        """Record agent output fields without finalizing the job (allows rework).

        List/dict fields (``modified_files``, ``token_usage``) are JSON-encoded
        because the ``jobs`` columns are TEXT.
        """
        upd = {}
        for k in ("modified_files", "token_usage", "tool_calls", "container_id",
                  "command", "commit_sha", "ci_status", "model", "role", "pr_number",
                  "round"):
            if k in result and result[k] is not None:
                v = result[k]
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
        if job["state"] not in ("claimed", "running"):
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
        probe = self.conn.execute(
            "SELECT 1 FROM jobs WHERE state IN ('claimed','running') "
            "AND lease_expires IS NOT NULL AND lease_expires < ? LIMIT 1",
            (now,)).fetchone()
        if not probe:
            return 0
        cur = self.conn.execute(
            """UPDATE jobs SET state='pending', worker_token_hash=NULL, lease_expires=NULL
               WHERE state IN ('claimed','running') AND lease_expires IS NOT NULL
               AND lease_expires < ?""",
            (now,))
        self.conn.commit()
        return cur.rowcount

    @_retry_on_locked
    def claim(self, token: str) -> dict:
        h = self._check_token(token)
        self.reap_expired_leases()
        # 1) Re-claim an already-owned (claimed/running/agent_done) job — idempotent.
        #    'agent_done' is re-claimable so a round-2 rework can pick the same
        #    task back up after the scheduler signals rework.
        cur = self.conn.execute(
            "SELECT id FROM jobs WHERE state IN ('claimed','running','agent_done') "
            "AND worker_token_hash=? LIMIT 1", (h,))
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

    def post_events(self, token: str, job_id: int, events: list) -> dict:
        h = self._check_token(token)
        job = self._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        seq = 0
        accepted = 0
        for ev in events:
            etype = ev.get("type")
            eid = ev.get("id")
            payload = json.dumps(ev.get("payload", {}))
            try:
                if eid:
                    self.conn.execute(
                        "INSERT INTO events(job_id, event_type, seq, event_id, ts, payload) "
                        "VALUES (?,?,?,?,?,?)",
                        (job_id, etype, seq, eid, self._now(), payload))
                else:
                    self.conn.execute(
                        "INSERT INTO events(job_id, event_type, seq, ts, payload) "
                        "VALUES (?,?,?,?,?)",
                        (job_id, etype, seq, self._now(), payload))
                accepted += 1
            except sqlite3.IntegrityError:
                pass  # duplicate event_id -> idempotent no-op
            seq += 1
        self.conn.commit()
        return {"ok": True, "accepted": accepted}

    def append_event(self, job_id: int, event: dict) -> None:
        """Internal event append (no worker-auth) for routing follow-ups.

        Used by the event router to attach an ``issue_comment`` follow-up to an
        existing issue task. Idempotent by ``event["id"]`` (duplicate comment →
        no-op). Does NOT require a worker token (it is control-plane-internal).
        """
        job = self._get_job(job_id)
        etype = event.get("type")
        eid = event.get("id")
        payload = json.dumps(event.get("payload", {}))
        try:
            self.conn.execute(
                "INSERT INTO events(job_id, event_type, seq, event_id, ts, payload) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, etype, 0, eid, self._now(), payload))
        except sqlite3.IntegrityError:
            pass  # duplicate follow-up id -> idempotent no-op
        self.conn.commit()

    def _finish(self, token, job_id, final_state, updates: dict) -> dict:
        h = self._check_token(token)
        job = self._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        if job["state"] in ("completed", "failed"):
            return {"ok": True, "already_finished": True, "state": job["state"]}
        set_cols = ", ".join(f"{k}=?" for k in updates)
        params = [final_state, self._now(), *updates.values(), job_id]
        self.conn.execute(
            f"UPDATE jobs SET state=?, ended_at=?, {set_cols} WHERE id=?", params)
        self.conn.commit()
        return {"ok": True, "state": final_state}

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

    def list_jobs(self, state: Optional[str] = None) -> list:
        if state:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE state=?", (state,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
