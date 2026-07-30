#!/usr/bin/env python3
"""JSONL / log rotation for Hermes run logs.

For every ``*.jsonl`` / ``*.log`` file in LOGDIR whose size >= MAX_BYTES:
  - rename to ``<file>.<utc-timestamp>`` and gzip it;
  - prune oldest gzipped copies beyond KEEP.
Cross-platform (no external binaries required), so it runs in the safe
offline test environment as well as on the cloud server.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import gzip
import os


def rotate(logdir: str, keep: int = 5, max_bytes: int = 5 * 1024 * 1024) -> list[str]:
    rotated: list[str] = []
    for pattern in ("*.jsonl", "*.log"):
        for path in glob.glob(os.path.join(logdir, pattern)):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < max_bytes:
                continue
            ts = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ")
            staged = f"{path}.{ts}"
            os.rename(path, staged)
            with open(staged, "rb") as src, open(staged + ".gz", "wb") as dst:
                dst.write(gzip.compress(src.read()))
            os.remove(staged)
            rotated.append(staged + ".gz")
    # Global prune: keep only the newest `keep` rotated archives in the dir.
    all_gz = sorted(glob.glob(os.path.join(logdir, "*.gz")),
                    key=os.path.getmtime, reverse=True)
    for old in all_gz[keep:]:
        os.remove(old)
    return rotated


def main() -> None:
    ap = argparse.ArgumentParser(description="Hermes JSONL log rotation")
    ap.add_argument("logdir", help="directory containing *.jsonl / *.log")
    ap.add_argument("--keep", type=int, default=5)
    ap.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    args = ap.parse_args()
    rotated = rotate(args.logdir, args.keep, args.max_bytes)
    if rotated:
        print("ROTATED:")
        for r in rotated:
            print(" ", r)
    else:
        print("ROTATION: nothing to rotate")


if __name__ == "__main__":
    main()
