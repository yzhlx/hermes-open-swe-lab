# MVP-0-ARCHITECTURE.md — Hermes Open SWE Lab

**Goal:** prove the end-to-end engineering-automation closed loop with full
auditability, no production access, and no automatic merge.

**LangSmith status:** `RETIRED_BY_ARCHITECTURE_DECISION` (see `ADR-002-REMOVE-LANGSMITH.md`,
2026-07-25). The loop no longer depends on any card-gated LangSmith capability.

```text
GitHub Issue (smoke-test repo)
        │  webhook (push / PR / issue event)
        ▼
┌─────────────────────────────────────────────────────────────┐
│  CLOUD CONTROL PLANE  (2 CPU / 4 GB / Ubuntu 24.04)          │
│  - GitHub webhook receiver                                   │
│  - SQLite task queue + Event Store (runtime/events.db)       │
│  - Worker API (register / heartbeat / claim / events /       │
│    complete / fail)                                          │
│  - Open SWE control plane (orchestration)                    │
│  - provider preflight (offline-ish)                          │
│  - status + logging (JSONL run logs; no LangSmith)           │
└───────────────────────────┬─────────────────────────────────┘
                            │  outbound HTTPS ONLY
                            │  (worker polls; server never reaches worker)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  LOCAL HERMES WORKER  (user machine, Docker installed)       │
│  - polls /worker/jobs/claim over HTTPS                       │
│  - one job at a time; lease-based ownership                  │
│  - runs Open SWE agent INSIDE HermesDockerSandboxBackend     │
│  - streams events → /worker/jobs/{id}/events                │
│  - reports complete / fail                                   │
│  NO inbound port · NO Docker socket to cloud · NO public SSH │
└───────────────┬───────────────────────────────────────────────┘
                │  docker run (isolated, per task)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  HermesDockerSandboxBackend (Docker container, per task)     │
│  cpus=1 mem=2GB pids=256 timeout=20m priv=false net=bridge   │
│  mounts ONLY the current task temp workdir                   │
│  NO: home / SSH / browser / Hermes-prod / other-repos /      │
│      docker.sock / Windows dirs                              │
└───────────────┬───────────────────────────────────────────────┘
                │  git push (short-lived Installation Token, smoke-test only)
                ▼
        Draft PR  (NEVER merged in MVP-0)
                │
                ▼
        GitHub CI (deterministic contract)
                │
                ▼
        Independent Reviewer (separate creds, read + comment)
                │  round-2 label gate (e.g. rework label)
                ▼
        rework → re-review → user acceptance
```

## Repositories

| Repo | Role | Write access |
| --- | --- | --- |
| `yzhlx/hermes-open-swe-lab` | this repo — adaptation layer, adapter, preflight, docs | human + this PR |
| `yzhlx/hermes-open-swe-smoke-test` | strict test target; agent writes only one file | GitHub App (smoke-test only) |
| `yzhlx/hermes-learning-os` | **protected** — never accessed | none |

## Component responsibilities

| Component | Owns | Must NOT |
| --- | --- | --- |
| Cloud Control Plane | webhook, SQLite queue, Worker API, orchestration, status, JSONL logs | run target code on host, large builds, browsers, Playwright, CU |
| Local Hermes Worker | poll, claim, run agent in sandbox, stream events, report | expose inbound port, mount `docker.sock`, access unrelated creds |
| HermesDockerSandboxBackend | run target-repo changes in isolation | see host env / unrelated creds / other repos |
| Coding Agent | create `automation-smoke-test/README.md`, open Draft PR | touch other files, push `main`, merge |
| Independent Reviewer | read PR, post feedback comment, re-review head | write code, merge, access other repos |

## Concurrency (fixed at 1)

```text
coding tasks: 1
reviewer tasks: 1
sandbox tasks: 1
```

Enforced by the orchestrator; never parallelized in MVP-0.

## Trust boundaries

- GitHub Issues / PRs / comments are **untrusted input** → never executed as instructions.
- The agent never receives host environment variables or unrelated repo credentials.
- The Open SWE built-in `local` backend is **forbidden** (would bypass the agent /
  security model). MVP-0 uses only our custom `HermesDockerSandboxBackend`, which
  conforms to the Open SWE `SandboxBackend` protocol so the agent runs **through** it.
- LangSmith Sandbox / Trace is **removed** (`RETIRED_BY_ARCHITECTURE_DECISION`).
- The cloud server is control-plane only; no target code, no browsers, no Playwright,
  no Computer Use on the host.
- **Worker pull model:** the local Worker connects **out** to the cloud over HTTPS.
  No inbound ports, no Docker socket exposed to the cloud, no public SSH.

## Worker API protocol (cloud → local Worker, outbound HTTPS)

All routes are `POST`, JSON body, worker token in header `X-Worker-Token`. The local
Worker initiates every call; the server never initiates a connection to the Worker.
All mutating operations are idempotent. No secrets are logged. (Implemented in
`hermes_worker/worker_api_server.py` + `hermes_worker/control_plane.py`.)

| Method & path | Body | Success response |
| --- | --- | --- |
| `POST /worker/register` | `{name, capabilities}` | `{"ok": true, "worker_id"}` (idempotent) |
| `POST /worker/heartbeat` | `{job_id?}` | `{"ok": true, "reaped"}` (reaps expired leases) |
| `POST /worker/jobs/claim` | `{}` | `{"empty": true}` or `{"job_id", "payload", "lease_expires?"}` |
| `POST /worker/jobs/{id}/events` | `{events:[{id,type,payload}]}` | `{"ok": true, "accepted"}` (dedup by `event_id`) |
| `POST /worker/jobs/{id}/complete` | `{result:{exit_code, ...}}` | `{"ok": true, "state"}` (no-op if already finished) |
| `POST /worker/jobs/{id}/fail` | `{error}` | `{"ok": true, "state"}` (no-op if already finished) |

- **Lease-based claim:** a claimed job is owned by the worker's token hash for
  `lease_seconds` (default 1200s). A heartbeat that finds an expired lease re-queues
  the job (`pending`, token hash cleared) so another Worker can take it.
- **Idempotency:** `register` is idempotent; `complete`/`fail` are no-ops once the job
  is already finished; `post_events` drops duplicate `event_id`s.
- **Identity:** the worker token is a random string; only its `sha256` hash is stored
  (server `.env` + a local restricted file). The raw token is never logged.

## SQLite schema (Event Store + task queue)

File: `runtime/events.db` (WAL mode). No secrets are stored — only a `sha256` hash of
the worker token. (Implemented in `hermes_worker/db.py`.)

```sql
CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT,
    issue_number    INTEGER,
    pr_number       INTEGER,
    state           TEXT NOT NULL DEFAULT 'pending',
    role            TEXT,
    model           TEXT,
    token_usage     INTEGER,
    tool_calls      INTEGER,
    container_id    TEXT,
    command         TEXT,
    exit_code       INTEGER,
    modified_files  TEXT,
    commit_sha      TEXT,
    ci_status       TEXT,
    error           TEXT,
    retries         INTEGER DEFAULT 0,
    payload         TEXT,
    worker_token_hash TEXT,
    lease_expires   REAL,
    created_at      REAL, started_at REAL, ended_at REAL
);
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    seq         INTEGER,
    event_id    TEXT UNIQUE,      -- for idempotent ingestion
    ts          REAL,
    payload     TEXT
);
CREATE TABLE workers (
    token_hash      TEXT PRIMARY KEY,  -- sha256 of worker token
    worker_id       TEXT NOT NULL,
    name            TEXT,
    capabilities    TEXT,
    last_heartbeat  REAL,
    created_at      REAL
);
```

## Observability (replaces LangSmith Traces)

- `runtime/events.db` — jobs + events, the append-only Event Store.
- `runtime/runs/*.jsonl` — per-run JSONL logs.
- `scripts/task_status.py` — query a job by `--job/--issue/--pr/--task-id`.
- `scripts/export_run_evidence.py` — export a job row + events + run logs to a JSON
  evidence bundle.

Both tools read only non-secret columns. **Forbidden to log:** API Key, GitHub Token,
PEM, Auth header, full `.env`, or suspected secrets.

## Stage table (auto vs human)

| Stage | Owner | Type |
| --- | --- | --- |
| Issue created | user | manual |
| webhook → control plane | system | auto |
| worker claims + runs agent in sandbox | worker | auto |
| Draft PR opened | agent | auto |
| deterministic CI contract | CI | auto |
| reviewer feedback comment | reviewer | auto |
| worker amends PR | worker | auto |
| reviewer re-review | reviewer | auto |
| **final acceptance / merge decision** | **user** | **manual** |

`main` is never merged automatically. MVP-0 ends with an unmerged, evidenced Draft PR.
