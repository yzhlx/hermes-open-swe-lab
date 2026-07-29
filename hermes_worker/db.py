"""SQLite task queue + Event Store for Hermes Open SWE MVP-0.

Security: NO secrets are ever stored here. Only a sha256 hash of the worker
token is kept, for lease-ownership checks. The real token lives only in the
server ``.env`` and a local restricted file (see control_plane.py).

Observability columns mirror the architecture decision (task_id, issue/pr,
state machine, role, model, token usage, tool calls, container id, command,
exit code, modified files, commit SHA, CI status, errors, retries, timestamps).
"""
from __future__ import annotations

import os
import sqlite3
import json
import hashlib


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT,
    repo            TEXT,
    issue_number    INTEGER,
    pr_number       INTEGER,
    state           TEXT NOT NULL DEFAULT 'pending',
    control_state   TEXT NOT NULL DEFAULT 'active',
    version         INTEGER NOT NULL DEFAULT 0,
    requirements    TEXT,
    requirements_revision INTEGER NOT NULL DEFAULT 0,
    request_fingerprint TEXT,
    role            TEXT,
    model           TEXT,
    round           INTEGER DEFAULT 0,
    token_usage     INTEGER,
    tool_calls      INTEGER,
    container_id    TEXT,
    command         TEXT,
    exit_code       INTEGER,
    modified_files  TEXT,
    commit_sha      TEXT,
    ci_status       TEXT,
    error           TEXT,
    retries         INTEGER DEFAULT 0,
    payload         TEXT,
    worker_token_hash TEXT,
    lease_expires   REAL,
    created_at      REAL,
    started_at      REAL,
    ended_at        REAL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    seq         INTEGER,
    event_id    TEXT UNIQUE,
    ts          REAL,
    payload     TEXT,
    source_type TEXT NOT NULL DEFAULT 'legacy',
    source_id   TEXT NOT NULL DEFAULT 'unverified',
    actor_role  TEXT,
    channel     TEXT,
    display_trust TEXT NOT NULL DEFAULT 'unverified'
);
CREATE TABLE IF NOT EXISTS workers (
    token_hash      TEXT PRIMARY KEY,
    worker_id       TEXT NOT NULL,
    name            TEXT,
    capabilities    TEXT,
    last_heartbeat  REAL,
    created_at      REAL
);
-- Replay-protection nonces (worker API). One row per accepted request nonce,
-- expired after the replay window. A reused nonce fails the UNIQUE constraint.
CREATE TABLE IF NOT EXISTS nonces (
    nonce   TEXT PRIMARY KEY,
    expires REAL
);
-- Webhook delivery dedup (GitHub X-GitHub-Delivery). Reused id -> no new job.
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    ts          REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
CREATE INDEX IF NOT EXISTS idx_workers_hb ON workers(last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_nonces_exp ON nonces(expires);
CREATE INDEX IF NOT EXISTS idx_deliveries_ts ON deliveries(ts);
-- Task idempotency: one job per (repo, issue_number). A second webhook for the
-- same issue hits the UNIQUE constraint and is collapsed onto the existing job
-- (no duplicate task, no duplicate PR — D3 requirement #25).
CREATE TABLE IF NOT EXISTS issue_tasks (
    repo          TEXT NOT NULL,
    issue_number  INTEGER NOT NULL,
    job_id        INTEGER NOT NULL,
    created_at    REAL,
    PRIMARY KEY (repo, issue_number)
);
CREATE TABLE IF NOT EXISTS operator_actions (
    job_id       INTEGER NOT NULL,
    request_id   TEXT NOT NULL,
    action       TEXT NOT NULL,
    request_fingerprint TEXT,
    result       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (job_id, request_id)
);
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the live column names for one known schema table."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive, idempotent migrations required by the workbench.

    Existing event rows predate source attribution.  They are deliberately
    labelled ``legacy`` / ``unverified`` instead of being upgraded to a
    stronger provenance claim.
    """
    job_columns = _table_columns(conn, "jobs")
    event_columns = _table_columns(conn, "events")
    operator_action_columns = _table_columns(conn, "operator_actions")
    needs_job_column = "control_state" not in job_columns
    needs_version = "version" not in job_columns
    needs_requirements = "requirements" not in job_columns
    needs_requirements_revision = "requirements_revision" not in job_columns
    needs_job_request_fingerprint = (
        "request_fingerprint" not in job_columns
    )
    needs_source_type = "source_type" not in event_columns
    needs_source_id = "source_id" not in event_columns
    needs_actor_role = "actor_role" not in event_columns
    needs_channel = "channel" not in event_columns
    needs_display_trust = "display_trust" not in event_columns
    needs_action_request_fingerprint = (
        "request_fingerprint" not in operator_action_columns
    )
    needs_job_backfill = (
        not needs_job_column
        and not needs_version
        and not needs_requirements_revision
        and conn.execute(
            "SELECT 1 FROM jobs WHERE control_state IS NULL OR control_state='' "
            "OR version IS NULL OR requirements_revision IS NULL LIMIT 1"
        ).fetchone() is not None
    )
    needs_event_backfill = (
        not needs_source_type
        and not needs_source_id
        and not needs_actor_role
        and not needs_channel
        and not needs_display_trust
        and conn.execute(
            "SELECT 1 FROM events WHERE source_type IS NULL OR source_type='' "
            "OR source_id IS NULL OR source_id='' "
            "OR display_trust IS NULL OR display_trust='' LIMIT 1"
        ).fetchone() is not None
    )
    if not any((
        needs_job_column,
        needs_version,
        needs_requirements,
        needs_requirements_revision,
        needs_job_request_fingerprint,
        needs_source_type,
        needs_source_id,
        needs_actor_role,
        needs_channel,
        needs_display_trust,
        needs_action_request_fingerprint,
        needs_job_backfill,
        needs_event_backfill,
    )):
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        job_columns = _table_columns(conn, "jobs")
        if "control_state" not in job_columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN control_state "
                "TEXT NOT NULL DEFAULT 'active'"
            )
        if "version" not in job_columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN version "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "requirements" not in job_columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN requirements TEXT"
            )
        if "requirements_revision" not in job_columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN requirements_revision "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "request_fingerprint" not in job_columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN request_fingerprint TEXT"
            )
        conn.execute(
            "UPDATE jobs SET control_state='active' "
            "WHERE control_state IS NULL OR control_state=''"
        )
        conn.execute(
            "UPDATE jobs SET version=0 WHERE version IS NULL"
        )
        conn.execute(
            "UPDATE jobs SET requirements_revision=0 "
            "WHERE requirements_revision IS NULL"
        )

        event_columns = _table_columns(conn, "events")
        if "source_type" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN source_type "
                "TEXT NOT NULL DEFAULT 'legacy'"
            )
        if "source_id" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN source_id "
                "TEXT NOT NULL DEFAULT 'unverified'"
            )
        if "actor_role" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN actor_role TEXT"
            )
        if "channel" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN channel TEXT"
            )
        if "display_trust" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN display_trust "
                "TEXT NOT NULL DEFAULT 'unverified'"
            )
        conn.execute(
            "UPDATE events SET source_type='legacy' "
            "WHERE source_type IS NULL OR source_type=''"
        )
        conn.execute(
            "UPDATE events SET source_id='unverified' "
            "WHERE source_id IS NULL OR source_id=''"
        )
        conn.execute(
            "UPDATE events SET display_trust='unverified' "
            "WHERE display_trust IS NULL OR display_trust=''"
        )
        operator_action_columns = _table_columns(
            conn, "operator_actions"
        )
        if "request_fingerprint" not in operator_action_columns:
            conn.execute(
                "ALTER TABLE operator_actions "
                "ADD COLUMN request_fingerprint TEXT"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Autocommit (isolation_level=None) so every statement is its own
    # transaction. This avoids the deferred read-transaction -> write-upgrade
    # stall that makes concurrent claims pathologically slow under WAL, and
    # keeps the single-statement atomic claims truly atomic.
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL;")
    # Concurrent writers (MVP concurrency = 1 per type, but the control plane
    # still services several short-lived writer connections, and the offline
    # concurrency test stresses many) must WAIT for the write lock instead of
    # failing with "database is locked". 15s absorbs brief WAL handoff bursts.
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    return conn


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn
