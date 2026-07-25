#!/usr/bin/env bash
# rotate_logs.sh — thin wrapper around rotate_logs.py (JSONL log rotation).
# Usage: rotate_logs.sh <logdir> [keep] [max_bytes]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/rotate_logs.py" "$@"
