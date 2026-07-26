# Cloud Control Plane Deployment (line C — `d3-deployment`)

This document covers the **repeatable, safe deployment** of the Hermes Open SWE
Cloud Control Plane. It is independent of the D3 core state machine and the
relay model adapter (those are Line A's code, consumed here as libraries).

> **MVP-0 posture:** the service is deployed **disabled** or **bound to
> `127.0.0.1` only**. No public business port is opened and the GitHub Webhook
> is **off**. TLS is terminated by the reverse proxy (see `reverse-proxy/`).

## Assets delivered

| Asset | Path | Purpose |
| --- | --- | --- |
| Deploy adapter (health + safety) | `deploy/cloud/control_plane_app.py` | Run the Control Plane; exposes `/healthz`, `/readyz`, and the Worker API; enforces no-root + loopback-only. |
| systemd unit | `systemd/hermes-swe-control-plane.service` | `hermes-swe` user, auto-restart, hardening, `ExecStartPre` config check. |
| EnvironmentFile (template) | `systemd/hermes-swe-control-plane.env.example` | Non-secret config only (placeholders). |
| Deploy / upgrade | `scripts/deploy_control_plane.sh` | Idempotent install; leaves the unit **disabled**. |
| Rollback | `scripts/rollback_control_plane.sh` | Re-deploy a previous committed SHA. |
| Config check (fail-closed) | `scripts/check_config.sh` | Refuses root / wrong user / non-loopback / missing config / secrets-in-env. |
| SQLite backup | `scripts/backup_sqlite.py` | WAL checkpoint + integrity-checked online backup. |
| SQLite restore | `scripts/restore_sqlite.py` | Fail-closed restore (`--force` required to overwrite). |
| Log rotation | `scripts/rotate_logs.py` (+ `.sh`) | JSONL / `.log` rotation + gzip + prune. |
| Reverse proxy | `reverse-proxy/nginx-https.conf` | HTTPS only; `:80`→`:443` redirect; no webhook location. |
| Firewall | `docs/deployment/FIREWALL.md` | Minimal `ufw` rules. |

## Install / upgrade

```bash
# As an operator (sudo/root), from the deploy source checkout:
export HERMES_APP_HOME=/opt/hermes-open-swe-lab
export HERMES_DEPLOY_SRC=/path/to/checked-out/repo
export HERMES_SERVICE_USER=hermes-swe
export HERMES_AUTOSTART=0          # default: disabled. Set 1 to start on 127.0.0.1.

sudo -E bash scripts/deploy_control_plane.sh
```

The script is **idempotent** — re-running it upgrades in place (copy + perms +
config check) without downtime logic changes. Re-running twice yields the same
result (verified by `tests/deployment/test_deploy_idempotency.py`).

## Start / stop / restart

```bash
sudo systemctl start   hermes-swe-control-plane.service
sudo systemctl status  hermes-swe-control-plane.service
sudo systemctl restart hermes-swe-control-plane.service   # Restart=on-failure, TimeoutStartSec=30
sudo systemctl stop    hermes-swe-control-plane.service
```

`Restart=on-failure` + `RestartSec=5` provide auto-recovery; `TimeoutStartSec=30`
bounds startup. The app also handles `SIGTERM` for graceful shutdown.

## Health

```bash
curl -fsS http://127.0.0.1:8080/healthz   # -> {"status":"ok",...}
curl -fsS http://127.0.0.1:8080/readyz    # -> {"ready":true} (503 if DB unreadable)
```

(Over the network the Worker reaches these via `https://<domain>/...` through
the reverse proxy.)

## Backup / restore

```bash
# Backup (online, integrity-checked):
python3 scripts/backup_sqlite.py --src runtime/events.db --dst-dir backups/

# Restore (service stopped first; --force required to overwrite live DB):
sudo systemctl stop hermes-swe-control-plane.service
python3 scripts/restore_sqlite.py --backup backups/events-<ts>.db \
        --target runtime/events.db --force
```

## Rotate logs

```bash
python3 scripts/rotate_logs.py logs/ --keep 5 --max-bytes 5242880
```

## Rollback

```bash
# To the previous deployed SHA (from DEPLOY_HISTORY):
sudo -E bash scripts/rollback_control_plane.sh

# Or to an explicit SHA:
sudo -E bash scripts/rollback_control_plane.sh <commit-sha>
```

Rollback stops the service, checks out the target in the deploy source, and
re-runs the idempotent deploy (copy + perms + config check). It does **not**
auto-start unless `HERMES_AUTOSTART=1`.

## Fail-closed guarantees

- `check_config.sh` exits non-zero on any violation; `systemd` aborts the start.
- The app refuses to start as **root** or on a **non-loopback** bind.
- No secret is ever read from the EnvironmentFile; secrets live in a `600`
  `.env` loaded by the application.

## What this line does NOT do

- Does **not** implement the D3 core state machine, Webhook verification, claim
  logic, Token Broker, or Reviewer state machine (Line A owns those files).
- Does **not** modify `hermes_worker/control_plane.py`,
  `worker_api_server.py`, `db.py`, `webhook_receiver.py`.
- Does **not** enable the Webhook, generate a Webhook secret, or mint a real
  Installation Token.
- Does **not** modify DNS, purchase certificates, or open a public webhook port.
