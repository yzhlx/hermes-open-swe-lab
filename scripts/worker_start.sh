#!/usr/bin/env bash
# worker_start.sh — launch the local Hermes Worker (WSL / Linux).
#
# Safety model:
#   - The Worker makes ONLY outbound HTTPS calls; it never opens an inbound
#     port, never mounts the Docker socket to the cloud, and never requires
#     public SSH.
#   - Local restricted config (token path, cloud URL) is loaded from
#     ~/.hermes/worker.env (never committed). Secrets are not echoed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

if [ -f "$HOME/.hermes/worker.env" ]; then
  set -a; . "$HOME/.hermes/worker.env"; set +a
fi

export PYTHONPATH="$HERE:${PYTHONPATH:-}"
# Docker preflight (when HERMES_WORKER_BACKEND=docker) is performed inside the
# worker runner so it fails clearly if the daemon is down.
exec python3 -m deploy.worker.worker_runner
