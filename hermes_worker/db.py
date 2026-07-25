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
    payload     TEXT
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
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    return conn


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn
