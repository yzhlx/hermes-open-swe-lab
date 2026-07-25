#!/usr/bin/env python3
"""Export a run's evidence bundle (job row + events + runs/*.jsonl) to JSON.

Usage:
  python scripts/export_run_evidence.py --db runtime/events.db --job 1 --out evidence/job1.json

The bundle contains only non-secret observability data (state machine, role,
model, token usage, tool calls, container id, command, exit code, modified
files, commit SHA, CI status, errors, retries, timestamps, event stream). It
never includes API keys, tokens, PEMs, or Authorization headers.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.db import connect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="runtime/events.db")
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs-dir", default="runtime/runs")
    args = ap.parse_args()

    conn = connect(args.db)
    job = dict(conn.execute("SELECT * FROM jobs WHERE id=?",
                            (int(args.job),)).fetchone())
    events = [dict(e) for e in conn.execute(
        "SELECT * FROM events WHERE job_id=? ORDER BY id",
        (int(args.job),)).fetchall()]

    bundle = {"job": job, "events": events, "runs_jsonl": []}
    if os.path.isdir(args.runs_dir):
        for fn in sorted(os.listdir(args.runs_dir)):
            if fn.endswith(".jsonl") and fn.startswith(f"job{args.job}"):
                with open(os.path.join(args.runs_dir, fn), encoding="utf-8") as f:
                    bundle["runs_jsonl"].append(f.read().splitlines())

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, default=str)
    print(f"Evidence written to {args.out} "
          f"(state={job['state']}, events={len(events)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
