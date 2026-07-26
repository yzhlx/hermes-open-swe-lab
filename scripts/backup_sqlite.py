#!/usr/bin/env python3
"""Safe SQLite (WAL) online backup for the Hermes Control Plane Event Store.

Produces a timestamped, integrity-checked copy of the WAL database. Uses
``PRAGMA wal_checkpoint(TRUNCATE)`` so all committed data is folded into the
main database file before the copy, then ``Connection.backup`` for an
atomic-consistent snapshot. No secrets are stored in the database, so the
backup is safe to keep.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys


def backup(src: str, dst_dir: str) -> str:
    os.makedirs(dst_dir, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = os.path.join(dst_dir, f"events-{ts}.db")
    src_conn = sqlite3.connect(src)
    try:
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
            rows = dst_conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    if any(r[0] != "ok" for r in rows):
        print("BACKUP INTEGRITY FAILURE:", rows, file=sys.stderr)
        sys.exit(1)
    os.chmod(dst, 0o640)
    print(f"BACKUP OK {dst}")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="Hermes SQLite WAL backup")
    ap.add_argument("--src", default=os.environ.get("HERMES_DB_PATH",
                                                     "runtime/events.db"))
    ap.add_argument("--dst-dir", default=os.environ.get("HERMES_BACKUP_DIR",
                                                         "backups"))
    args = ap.parse_args()
    if not os.path.exists(args.src):
        print(f"BACKUP SKIP: source db not found: {args.src}", file=sys.stderr)
        sys.exit(0)
    backup(args.src, args.dst_dir)


if __name__ == "__main__":
    main()
