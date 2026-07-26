# Deployment ↔ Line A Integration Contract

**Line:** C (`d3-deployment`) — deployment assets only.
**Baseline:** `phase-1-smoke` @ `9d1d6356b039f0cc29278781486ddfb2831df8e4`.
**Status:** Draft for review by Line A. This file is the single source of truth
for the interface between the deployment line and the D3 core line.

## 1. Division of ownership

### Line A owns (MUST NOT be modified by line C)

| File | Owns |
| --- | --- |
| `hermes_worker/control_plane.py` | D3 core state machine (queue, lease, events, worker registry). |
| `hermes_worker/worker_api_server.py` | Worker API business routes. |
| `hermes_worker/webhook_receiver.py` | Webhook signature verification. |
| `hermes_worker/db.py` | SQLite schema + WAL init. |
| `hermes_worker/worker.py` | `HermesWorker` client (claim/run/report). |
| claim transaction logic / Token Broker / Reviewer state machine | business logic (wherever Line A places it). |

Line C **consumes these as libraries**; it never edits them.

### Line C owns (this branch)

`deploy/cloud/control_plane_app.py`, `systemd/`, `scripts/deploy_*`,
`scripts/backup_*`, `scripts/restore_sqlite.py`, `scripts/check_config.sh`,
`scripts/rotate_logs.*`, `reverse-proxy/`, `deploy/worker/*`,
`scripts/worker_*`, `docs/deployment/*`, `tests/deployment/*`.

## 2. Runtime interface (what Line C starts, what Line A must expose)

### 2.1 Process entry point

- **Deploy adapter (for MVP-0 verification):** `python -m deploy.cloud.control_plane_app`
  — implements the full endpoint surface by delegating to `ControlPlane`.
- **Line A's future real server:** MUST expose the **same** endpoint surface and
  honour the **same** env vars in §2.3. It may replace the deploy adapter as the
  systemd `ExecStart` target, provided the contract below is met.

### 2.2 HTTP endpoint surface (must be stable)

| Method & path | Body | Success |
| --- | --- | --- |
| `GET /healthz` | — | `200 {"status":"ok"}` |
| `GET /readyz` | — | `200 {"ready":true}` / `503 {"ready":false,...}` |
| `POST /worker/register` | `{name, capabilities}` | `{"ok":true,"worker_id"}` |
| `POST /worker/heartbeat` | `{job_id?}` | `{"ok":true,"reaped"}` |
| `POST /worker/jobs/claim` | `{}` | `{"empty":true}` or `{"job_id","payload","lease_expires?}` |
| `POST /worker/jobs/{id}/events` | `{events:[{id,type,payload}]}` | `{"ok":true,"accepted"}` |
| `POST /worker/jobs/{id}/complete` | `{result:{...}}` | `{"ok":true,"state"}` |
| `POST /worker/jobs/{id}/fail` | `{error}` | `{"ok":true,"state"}` |

All `POST` routes: JSON body, worker token in header `X-Worker-Token`. The
Worker token is **never** logged; only its `sha256` hash is stored (in
`workers.token_hash`).

### 2.3 Environment contract (systemd EnvironmentFile + app)

| Variable | Required | Meaning |
| --- | --- | --- |
| `HERMES_DB_PATH` | yes | SQLite path (default `runtime/events.db`). |
| `HERMES_RUNTIME_DIR` | yes | Writable runtime dir. |
| `HERMES_LOG_DIR` | yes | Writable log dir. |
| `HERMES_LISTEN_HOST` | yes | **Must be loopback** (`127.0.0.1`/`::1`/`localhost`). |
| `HERMES_LISTEN_PORT` | yes | Listen port (default `8080`). |
| `HERMES_LOCALHOST_TEST` | no | `1` = explicit local plaintext test mode; default `0`. |
| `PYTHONPATH` | yes | Must include the install root so `deploy` + `hermes_worker` import. |
| `HERMES_SERVICE_USER` | (set by unit) | Enforced identity (`hermes-swe`). |

**Secrets** (API key, relay key, GitHub App PEM, webhook secret, raw worker
token) are **not** in the EnvironmentFile. They live in a `600` `.env` loaded by
the application. `check_config.sh` rejects any secret var present in the env
file.

### 2.4 Data store

- SQLite at `HERMES_DB_PATH`, **WAL mode** (`PRAGMA journal_mode=WAL`).
- Schema owned by Line A (`db.py`); Line C's `backup_sqlite.py` /
  `restore_sqlite.py` operate on it generically (no schema assumptions beyond a
  single DB file + `-wal`/`-shm` siblings).
- **No secrets** are ever stored in the DB (only the worker token hash).

### 2.5 Local Worker client

- Line C's `deploy/worker/worker_runner.py` drives `hermes_worker.HermesWorker`
  (library). Line A must keep `HermesWorker(base_url, token, backend=...)` and
  `run_once()` / `register()` / `heartbeat()` / `claim()` signatures stable, or
  bump this contract version.
- Worker reaches the Cloud at `HERMES_CLOUD_URL` (**https://** in production,
  `http://127.0.0.1` only when `HERMES_LOCALHOST_TEST=1`).

## 3. Joint hard gates (both lines must honour)

1. No public plaintext HTTP (Cloud binds loopback; TLS at reverse proxy).
2. Worker API production mode accepts HTTPS only.
3. Localhost test mode is explicitly enabled (`HERMES_LOCALHOST_TEST=1`).
4. Docker socket is never exposed to the cloud; sandbox runs only on the Worker.
5. Target-repo code never runs on the cloud Control Plane host.
6. No Token / PEM / Auth header / `.env` content in logs.
7. Control Plane never runs as root.
8. Config files default to minimal permissions (`640` files / `750` dirs / `600`
   token + key).
9. Webhook stays OFF; no real Webhook secret or Installation Token is generated
   by the deployment line.

## 4. Open integration items (Line A must provide before real run)

- [ ] Real control-plane server entry point exposing §2.2 (replacing the deploy
      adapter, or extending it) and honouring §2.3.
- [ ] Webhook receiver wired behind the reverse proxy **only when** explicitly
      enabled by the user (remains OFF for MVP-0).
- [ ] Token Broker endpoint (`POST /internal/task/{id}/token`) for the Worker to
      fetch short-lived Installation Tokens (Line A owns; Worker calls it).
- [ ] Stable `HermesWorker` constructor / `run_once` signature (see §2.5).
- [ ] Go/no-go on `HERMES_LISTEN_HOST` remaining loopback-only (current design)
      vs. a private interface IP — confirm before any non-loopback bind.

Line C will not block on these; deployment is verifiable today via the deploy
adapter + `echo` backend + localhost test mode.
