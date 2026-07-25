#!/usr/bin/env python3
"""Print a job's observability summary from runtime/events.db.

Usage:
  python scripts/task_status.py --db runtime/events.db --job 1
  python scripts/task_status.py --db runtime/events.db --issue 42
  python scripts/task_status.py --db runtime/events.db --pr 7
  python scripts/task_status.py --db runtime/events.db --task-id T1

Never prints secrets: it reads only the SQLite Event Store columns, which hold
no API keys, tokens, PEMs, or Authorization headers.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.db import connect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="runtime/events.db")
    ap.add_argument("--job")
    ap.add_argument("--issue")
    ap.add_argument("--pr")
    ap.add_argument("--task-id")
    args = ap.parse_args()

    conn = connect(args.db)
    if args.job:
        q, params = "SELECT * FROM jobs WHERE id=?", (int(args.job),)
    elif args.issue:
        q, params = "SELECT * FROM jobs WHERE issue_number=?", (int(args.issue),)
    elif args.pr:
        q, params = "SELECT * FROM jobs WHERE pr_number=?", (int(args.pr),)
    elif args.task_id:
        q, params = "SELECT * FROM jobs WHERE task_id=?", (args.task_id,)
    else:
        print("Specify one of --job/--issue/--pr/--task-id")
        return 1

    rows = conn.execute(q, params).fetchall()
    if not rows:
        print("No matching job.")
        return 0

    cols = ("task_id", "issue_number", "pr_number", "role", "model",
            "token_usage", "tool_calls", "container_id", "command", "exit_code",
            "modified_files", "commit_sha", "ci_status", "error", "retries",
            "created_at", "started_at", "ended_at")
    for r in rows:
        r = dict(r)
        print("=" * 60)
        print(f"JOB #{r['id']}  state={r['state']}")
        for k in cols:
            print(f"  {k:14}: {r.get(k)}")
        evs = conn.execute(
            "SELECT event_type, ts FROM events WHERE job_id=? ORDER BY id",
            (r['id'],)).fetchall()
        print(f"  events        : {len(evs)} -> " +
              ", ".join(e['event_type'] for e in evs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
