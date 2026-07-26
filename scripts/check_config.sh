#!/usr/bin/env bash
# check_config.sh — fail-closed configuration pre-flight for the Hermes
# Control Plane. Used as systemd ExecStartPre and by the deploy script.
#
# SECURITY: This script never prints secret values. It only validates that
# the deployment configuration is safe to start. Any failure exits 1 so the
# service start is aborted (no partial/insecure run).
set -u

fail=0
err() { echo "CONFIG-ERROR: $*" >&2; fail=1; }

# --- Gate: never run the service as root ---
if [ "$(id -u)" -eq 0 ]; then
  err "refusing to run Control Plane as root; use the 'hermes-swe' service user"
fi

# --- Gate: must run as the designated service user when enforced ---
if [ -n "${HERMES_SERVICE_USER:-}" ] && [ "$(id -un)" != "${HERMES_SERVICE_USER}" ]; then
  err "must run as service user '${HERMES_SERVICE_USER}' (current: $(id -un))"
fi

# --- Required non-secret configuration ---
for v in HERMES_DB_PATH HERMES_RUNTIME_DIR HERMES_LOG_DIR HERMES_LISTEN_HOST HERMES_LISTEN_PORT; do
  if [ -z "${!v:-}" ]; then
    err "missing required config: $v"
  fi
done

# --- Gate: loopback-only bind (no public plaintext HTTP) ---
case "${HERMES_LISTEN_HOST:-}" in
  127.0.0.1|::1|localhost) ;;
  *) err "HERMES_LISTEN_HOST must be loopback (127.0.0.1); got '${HERMES_LISTEN_HOST:-}'" ;;
esac

# --- Gate: no secrets supplied via the EnvironmentFile ---
for s in HERMES_WORKER_TOKEN HERMES_API_KEY HERMES_RELAY_API_KEY \
        GITHUB_APP_PRIVATE_KEY WEBHOOK_SECRET HERMES_GITHUB_TOKEN; do
  if [ -n "${!s:-}" ]; then
    err "secret '$s' must NOT be supplied via EnvironmentFile; use the restricted .env"
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "check_config: FAILED (fail-closed)" >&2
  exit 1
fi
echo "check_config: OK"
exit 0
