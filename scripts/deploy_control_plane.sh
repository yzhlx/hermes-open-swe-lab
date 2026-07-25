#!/usr/bin/env bash
# deploy_control_plane.sh — idempotent install/upgrade of the Hermes Cloud
# Control Plane (line C). Safe to run repeatedly (upgrade == re-run).
#
# What it does:
#   1. create the 'hermes-swe' service user (idempotent, skips if present)
#   2. create runtime/log/tls/systemd dirs with safe perms
#   3. copy the source tree (excluding VCS/.cache) into the install root
#   4. set ownership + minimal file perms
#   5. create an isolated venv (best-effort)
#   6. run the fail-closed config check
#   7. enable the systemd unit (DISABLED by default — does NOT auto-start)
#
# It does NOT start the service unless HERMES_AUTOSTART=1, and the service
# always binds loopback only. No secrets are written by this script.
set -euo pipefail

APP_HOME="${HERMES_APP_HOME:-/opt/hermes-open-swe-lab}"
SRC="${HERMES_DEPLOY_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE_USER="${HERMES_SERVICE_USER:-hermes-swe}"
START="${HERMES_AUTOSTART:-0}"
ENV_FILE="$APP_HOME/systemd/hermes-swe-control-plane.env"

log() { echo "[deploy] $*"; }
warn() { echo "[deploy][warn] $*" >&2; }

# --- 1. service user (operator context; the SERVICE itself runs as hermes-swe) ---
if command -v id >/dev/null 2>&1 && ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  if command -v useradd >/dev/null 2>&1; then
    log "creating service user $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_HOME" "$SERVICE_USER" 2>/dev/null \
      || warn "useradd failed (offline/test?); skipping user creation"
  else
    warn "useradd unavailable; skipping service-user creation (test env)"
  fi
fi

# --- 2. runtime dirs ---
mkdir -p "$APP_HOME"/runtime "$APP_HOME"/logs "$APP_HOME"/tls "$APP_HOME"/systemd "$APP_HOME"/venv
mkdir -p "$APP_HOME"/hermes_worker "$APP_HOME"/deploy "$APP_HOME"/scripts

# --- 3. copy source tree (exclude VCS + caches) ---
log "copying source from $SRC -> $APP_HOME"
tar -C "$SRC" --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.workbuddy' --exclude='.venv' -cf - . | tar -C "$APP_HOME" -xf -

# --- 4. ownership + minimal perms ---
# Secret files (.env, tls/*) are EXCLUDED from the broad sweep and re-pinned to
# 600 so an idempotent re-run never downgrades them (Gate 7).
if command -v chown >/dev/null 2>&1; then
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_HOME" 2>/dev/null || warn "chown skipped"
fi
if command -v chmod >/dev/null 2>&1; then
  find "$APP_HOME" -type d -not -path '*/tls*' -exec chmod 750 {} + 2>/dev/null || true
  find "$APP_HOME" -type f -not -name '.env' -not -path '*/tls/*' \
    -exec chmod 640 {} + 2>/dev/null || true
  # scripts must stay executable
  chmod 750 "$APP_HOME"/scripts/*.sh "$APP_HOME"/scripts/*.py 2>/dev/null || true
  [ -f "$ENV_FILE" ] && chmod 640 "$ENV_FILE"
fi
# Re-pin secret paths (best-effort; requires root for tls ownership change).
chmod 600 "$APP_HOME/.env" 2>/dev/null || true
if [ -d "$APP_HOME/tls" ]; then
  chown root:"$SERVICE_USER" "$APP_HOME/tls" 2>/dev/null || true
  chmod 600 "$APP_HOME"/tls/* 2>/dev/null || true
fi

# --- 5. isolated venv (best-effort; app uses only stdlib) ---
if command -v python3 >/dev/null 2>&1 && [ ! -x "$APP_HOME/venv/bin/python" ]; then
  log "creating venv at $APP_HOME/venv"
  python3 -m venv "$APP_HOME/venv" 2>/dev/null || warn "venv creation skipped"
fi

# --- 6. fail-closed config check (as the service user if possible) ---
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi
export HERMES_SERVICE_USER="$SERVICE_USER"
if command -v systemctl >/dev/null 2>&1 && id -u "$SERVICE_USER" >/dev/null 2>&1; then
  runuser -u "$SERVICE_USER" -- "$APP_HOME/scripts/check_config.sh" \
    || { echo "[deploy][ERROR] config check failed; aborting" >&2; exit 1; }
else
  "$APP_HOME/scripts/check_config.sh" \
    || { echo "[deploy][ERROR] config check failed; aborting" >&2; exit 1; }
fi

# --- 7. enable unit (disabled by default) ---
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable hermes-swe-control-plane.service 2>/dev/null || warn "enable skipped"
  if [ "$START" = "1" ]; then
    log "starting service (HERMES_AUTOSTART=1)"
    systemctl start hermes-swe-control-plane.service 2>/dev/null || warn "start skipped"
  else
    log "service left DISABLED (default). Start manually when ready."
  fi
else
  warn "systemctl unavailable (test env); skipping unit enable/start"
fi

# --- record deployed SHA (for rollback) ---
if [ -d "$SRC/.git" ]; then
  SHA="$(git -C "$SRC" rev-parse HEAD)"
  echo "$SHA" > "$APP_HOME/DEPLOYED_SHA" 2>/dev/null || true
  echo "$SHA" >> "$APP_HOME/DEPLOY_HISTORY" 2>/dev/null || true
fi

log "deploy complete -> $APP_HOME"
