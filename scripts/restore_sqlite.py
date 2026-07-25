#!/usr/bin/env python3
"""Restore a Hermes Control Plane SQLite backup.

Fail-closed:
  - refuses to overwrite an existing target DB unless ``--force`` is given
    (so we never silently clobber live state);
  - verifies the backup integrity before restoring;
  - removes stale ``-wal``/``-shm`` siblings of the target so the restored
    snapshot is authoritative.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def restore(backup_path: str, target: str, force: bool) -> None:
    if not os.path.exists(backup_path):
        print(f"RESTORE FAIL: backup not found: {backup_path}", file=sys.stderr)
        sys.exit(1)

    # Verify backup integrity first.
    bk = sqlite3.connect(backup_path)
    try:
        rows = bk.execute("PRAGMA integrity_check").fetchall()
    finally:
        bk.close()
    if any(r[0] != "ok" for r in rows):
        print("RESTORE FAIL: backup integrity check failed:", rows, file=sys.stderr)
        sys.exit(1)

    if os.path.exists(target) and not force:
        print("RESTORE ABORTED: target exists and --force not given: "
              f"{target}\n(stop the service, then re-run with --force)",
              file=sys.stderr)
        sys.exit(2)

    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    # Remove any stale WAL siblings so the restored snapshot is authoritative.
    for sibling in (target + "-wal", target + "-shm"):
        if os.path.exists(sibling):
            os.remove(sibling)
    with open(backup_path, "rb") as src, open(target, "wb") as dst:
        dst.write(src.read())
    os.chmod(target, 0o640)
    print(f"RESTORE OK {target} <- {backup_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hermes SQLite restore")
    ap.add_argument("--backup", required=True, help="backup .db file")
    ap.add_argument("--target", default=os.environ.get("HERMES_DB_PATH",
                                                        "runtime/events.db"))
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing target DB (required for live restore)")
    args = ap.parse_args()
    restore(args.backup, args.target, args.force)


if __name__ == "__main__":
    main()
