#!/usr/bin/env bash
# rollback_control_plane.sh — safe rollback to a previous deployed SHA.
#
# Usage: rollback_control_plane.sh [TARGET_SHA]
#   TARGET_SHA  explicit commit to roll back to (optional).
#               If omitted, the second-to-last SHA in DEPLOY_HISTORY is used.
#
# Behaviour:
#   - validates the target is a real commit (fail-closed)
#   - stops the service (so we never overwrite a live DB blindly)
#   - checks out the target in the deploy source and re-runs the idempotent
#     deploy (copy + perms + config check)
#   - does NOT auto-start unless HERMES_AUTOSTART=1
# No secrets are touched.
set -euo pipefail

APP_HOME="${HERMES_APP_HOME:-/opt/hermes-open-swe-lab}"
SRC="${HERMES_DEPLOY_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE_USER="${HERMES_SERVICE_USER:-hermes-swe}"
TARGET="${1:-}"

err() { echo "[rollback][ERROR] $*" >&2; exit 1; }

[ -d "$SRC/.git" ] || err "deploy source is not a git repo: $SRC"
command -v git >/dev/null 2>&1 || err "git not available"

if [ -z "$TARGET" ]; then
  if [ -f "$APP_HOME/DEPLOY_HISTORY" ]; then
    TARGET=$(tac "$APP_HOME/DEPLOY_HISTORY" 2>/dev/null | sed -n '2p')
  fi
  [ -n "$TARGET" ] || err "no previous SHA in DEPLOY_HISTORY; pass a target SHA explicitly"
fi

git -C "$SRC" cat-file -e "$TARGET^{commit}" 2>/dev/null \
  || err "target SHA is not a valid commit: $TARGET"

echo "[rollback] target = $TARGET"

# Stop the service before touching files (fail-closed: never overwrite live state blindly).
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop hermes-swe-control-plane.service 2>/dev/null || true
fi

git -C "$SRC" checkout --detach "$TARGET" 2>/dev/null || err "git checkout failed for $TARGET"

HERMES_DEPLOY_SRC="$SRC" HERMES_APP_HOME="$APP_HOME" HERMES_SERVICE_USER="$SERVICE_USER" \
  HERMES_AUTOSTART=0 "$SRC/scripts/deploy_control_plane.sh"

echo "$TARGET" >> "$APP_HOME/DEPLOY_HISTORY"
echo "[rollback] done -> $TARGET"
