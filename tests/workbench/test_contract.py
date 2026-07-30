"""Strong contracts for event provenance, projections, and event cursors.

This suite is intentionally RED until the workbench projection/event-store
contract exists.  It is an executable specification, not mock acceptance.
"""
from __future__ import annotations

import sqlite3

from hermes_worker.db import init_db


SOURCE_COLUMNS = {"source_type", "source_id"}


def _event(event_id: str, event_type: str) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "source_type": "control_plane",
        "source_id": "workbench-contract-test",
        "payload": {"event_id": event_id},
    }


def test_new_event_persists_required_source_fields(control_plane):
    job_id = control_plane.create_job({"command": "source contract"})
    control_plane.append_event(job_id, _event("source-1", "task_created"))

    [stored] = control_plane.get_events(job_id)
    assert SOURCE_COLUMNS.issubset(stored), (
        "Every persisted event must expose source_type and source_id as "
        "top-level Event Store fields; payload-only provenance is insufficient."
    )
    assert stored["source_type"] == "control_plane"
    assert stored["source_id"] == "workbench-contract-test"


def test_legacy_event_schema_migrates_to_explicit_unverified_source(tmp_path):
    db_path = tmp_path / "legacy-events.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            seq INTEGER,
            event_id TEXT UNIQUE,
            ts REAL,
            payload TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO events(job_id, event_type, seq, event_id, ts, payload)
        VALUES (1, 'legacy_event', 0, 'legacy-1', 1.0, '{}')
        """
    )
    conn.commit()
    conn.close()

    migrated = init_db(str(db_path))
    try:
        columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(events)")
        }
        assert SOURCE_COLUMNS.issubset(columns), (
            "init_db() must migrate an existing events table; "
            "CREATE TABLE IF NOT EXISTS alone is not a migration."
        )
        row = migrated.execute(
            "SELECT source_type, source_id FROM events WHERE event_id='legacy-1'"
        ).fetchone()
        assert tuple(row) == ("legacy", "unverified"), (
            "Legacy rows must remain visible but explicitly unverified; "
            "migration must never fabricate an Agent or GitHub source."
        )
    finally:
        migrated.close()


def test_task_projection_keeps_workflow_and_connection_as_independent_axes(
    control_plane, fake_clock, worker_token, require_method
):
    job_id = control_plane.create_job({"command": "projection contract"})
    get_projection = require_method(control_plane, "get_task_projection")

    before_claim = get_projection(job_id, 30)
    assert before_claim["state"] == "pending"
    assert before_claim["control_state"] == "active"
    assert before_claim["connection_state"] == "agent_not_started"
    assert before_claim["is_cached"] is False

    claimed = control_plane.claim(worker_token)
    assert claimed["job_id"] == job_id
    online = get_projection(job_id, 30)
    assert online["state"] == "running"
    assert online["connection_state"] == "online"
    assert online["last_heartbeat_at"] == fake_clock.value

    fake_clock.advance(31)
    disconnected = get_projection(job_id, 30)
    assert disconnected["state"] == "running", (
        "A stale heartbeat changes connectivity, not workflow truth."
    )
    assert disconnected["connection_state"] == "disconnected"
    assert disconnected["is_cached"] is False


def test_event_cursor_is_strictly_after_database_id(
    control_plane, require_method
):
    job_id = control_plane.create_job({"command": "cursor contract"})
    for index in range(4):
        control_plane.append_event(
            job_id, _event(f"cursor-{index}", f"event_{index}")
        )

    get_after = require_method(control_plane, "get_events_after")
    first_page = get_after(job_id, 0, 2)
    assert [event["event_type"] for event in first_page] == [
        "event_0",
        "event_1",
    ]

    cursor = first_page[-1]["id"]
    second_page = get_after(job_id, cursor, 10)
    assert [event["event_type"] for event in second_page] == [
        "event_2",
        "event_3",
    ]
    assert all(event["id"] > cursor for event in second_page)
