# Local Hermes Worker Deployment (line C — `d3-deployment`)

The local Worker runs on the user machine (Windows / WSL). It is the **only**
place that runs the Docker sandbox. It never exposes an inbound port, never
mounts the Docker socket to the cloud, and never needs public SSH.

> **Pull model:** the Worker connects **out** to the Cloud over HTTPS. The
> Cloud never initiates a connection to the Worker.

## Assets delivered

| Asset | Path | Purpose |
| --- | --- | --- |
| Worker runner (safety wrapper) | `deploy/worker/worker_runner.py` | Single-instance lock, Docker preflight, token-file load, HTTPS-only, graceful shutdown, exponential backoff. |
| WSL / Linux launcher | `scripts/worker_start.sh` | Loads `~/.hermes/worker.env`, runs the runner. |
| Windows launcher | `scripts/worker_start.ps1` | Same, for PowerShell. |
| systemd user service | `deploy/worker/hermes-worker-local.service` | Startup-on-boot (WSL/Linux). |
| Task Scheduler XML | `deploy/worker/hermes-worker-task-scheduler.xml` | Startup-on-boot (Windows). |

## Local config (`~/.hermes/worker.env`, never committed)

```bash
HERMES_CLOUD_URL=https://your-domain.example.com      # https:// required in production
HERMES_WORKER_TOKEN_FILE=$HOME/.hermes/worker_token    # mode 600
HERMES_WORKER_BACKEND=echo        # echo (offline) | docker (real sandbox)
HERMES_LOCALHOST_TEST=0           # 1 ONLY for isolated local testing
HERMES_POLL_INTERVAL=5
```

Create the token file and lock it down:

```bash
mkdir -p ~/.hermes
printf '%s' "paste-a-long-random-worker-token-here" > ~/.hermes/worker_token
chmod 600 ~/.hermes/worker_token
```

The runner **refuses** to start if the token file is group/other-accessible.

## Run

```bash
# WSL / Linux
bash scripts/worker_start.sh

# Windows (PowerShell)
pwsh -File scripts/worker_start.ps1
```

## Behaviour guarantees

- **No inbound port** — the Worker only makes outbound `POST`s; it never binds.
- **HTTPS-only (production)** — if `HERMES_LOCALHOST_TEST != 1`, the Cloud URL
  must start with `https://`; otherwise the Worker exits immediately.
- **Docker preflight** — with `HERMES_WORKER_BACKEND=docker`, the daemon must be
  reachable (`docker info`); otherwise the Worker fails *clearly* (no hang).
- **Single-instance lock** — `/var/run/hermes-worker.lock` (or
  `HERMES_WORKER_LOCK`) prevents two Workers claiming the same identity; stale
  locks from dead PIDs are removed automatically.
- **Graceful shutdown** — `SIGTERM`/`SIGINT` stops the poll loop and releases
  the lock.
- **Exponential backoff** — when the Cloud is unreachable, the Worker backs off
  `1,2,4,8,…` (capped at 60s) and resumes automatically when connectivity
  returns. No secret is logged.

## Startup-on-boot

**WSL / Linux** (systemd user):

```bash
# Replace __INSTALL_ROOT__ and __CLOUD_DOMAIN__ in the unit, then:
mkdir -p ~/.config/systemd/user
cp deploy/worker/hermes-worker-local.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-worker-local.service
```

**Windows** (Task Scheduler, runs at logon, least-privilege):

```powershell
schtasks /Create /TN "Hermes Open SWE Worker" `
  /XML deploy/worker/hermes-worker-task-scheduler.xml
```

## Rotate worker logs

The runner writes to stderr/stdout; capture to a file and rotate with the shared
rotator:

```bash
python3 scripts/rotate_logs.py /var/log/hermes-worker/ --keep 5
```

## Boundaries

This line does **not** modify `hermes_worker/worker.py`, the claim logic, the
Token Broker, or the Reviewer state machine. It uses `HermesWorker` and the
sandbox backends as libraries. See `DEPLOYMENT-INTEGRATION-CONTRACT.md`.
