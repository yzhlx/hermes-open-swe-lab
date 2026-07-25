# INSTALLATION-STATUS.md — Hermes Open SWE Lab (MVP-0)

**Purpose:** Track the implementation/installation status of every MVP-0 component.
Updated as work progresses. Items that cannot be verified in this sandbox are
marked `NOT_TESTED` and tied to the authorization gate (Step 9) or cloud-server access.

Legend: `DONE` · `IN_PROGRESS` · `PENDING` · `NOT_TESTED` (needs auth/cloud) ·
`RETIRED_BY_ARCHITECTURE_DECISION`

---

## Component status

| # | Component | Status | Evidence / Note |
| --- | --- | --- | --- |
| 1 | Root `AGENTS.md` (guardrails) | DONE | 572 lines; verified vs Step-1 constraints |
| 2 | `.gitignore` (Section 7 patterns) | DONE | commit `8572f03` |
| 3 | README (UTF-8) | DONE | commit `8572f03` |
| 4 | Branch `phase-1-smoke` | DONE | created from `main` @ `04ed8cf` |
| 5 | Read-only audit (Step 3) | DONE | `INITIAL-AUDIT.md` |
| 6 | Upstream baseline pin | DONE | `UPSTREAM-BASELINE.md`; SHA reachable |
| 7 | Security boundaries doc | DONE | `SECURITY-BOUNDARIES.md` (updated for D0) |
| 8 | Smoke-test repo `yzhlx/hermes-open-swe-smoke-test` | DONE | private; contract system MERGED to main (PR #1, merge `5dba406`) |
| 9 | Relay adapter (opt-in) | DONE | `hermes_open_swe_relay/`; 17 offline unit tests PASS |
| 10 | Relay adapter unit tests | DONE | 17 tests PASS |
| 11 | `scripts/provider_preflight.py` | DONE | P1–P6; no-config run → NOT_TESTED (exit 2) |
| 12 | Live provider preflight (P1–P6) | NOT_TESTED | needs relay creds (Step 9) |
| 13 | GitHub App (create + install + scope) | DONE | App `4389778`; installed on smoke-test only; Install ID `148886992` (read-only App auth); scope verified (lab+learning-os 404) |
| 13b | GitHub App key landing on server | DONE | `hermes-swe` svc user created; key at `/etc/.../secrets/github-app-private-key.pem` (hermes-swe:hermes-swe 600); SHA-256 MATCH; `.env` written; server-side auth verify PASS |
| 13c | Webhook | OFF | intentionally disabled in MVP; no secret/URL configured |
| 14 | LangSmith Service Key landing + tracing | **RETIRED_BY_ARCHITECTURE_DECISION** | Removed from MVP-0 by `ADR-002-REMOVE-LANGSMITH.md` (2026-07-25): org-level Sandboxes feature disabled → `SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`; user has no card to enable. **Secret purge is HELD** on the user revoking the LangSmith Service Key from the web UI (see ADR-002 §3). Historical evidence kept in `LANGSMITH-SETUP.md`. No LangSmith feature ever passed. |
| 15 | Cloud control plane deploy | NOT_TESTED | needs cloud-server access |
| 16 | End-to-end loop (Issue→PR→review→rework) | NOT_TESTED | depends on 12–15 + D2/D3 |
| 17 | Draft PR to lab repo | PENDING | Step 12 — after live loop |
| 18 | Cloud server health monitor | NOT_TESTED | needs cloud-server access |
| 19 | Phase D1: SQLite task queue + Worker API + local Worker + `EchoSandboxBackend` | DONE | `hermes_worker/` package; **7 offline unit tests PASS**; server+worker+CLI integration smoke PASS (see ADR-002 §7) |
| 20 | Phase D1: observability CLI (`task_status.py`, `export_run_evidence.py`) | DONE | run against `events.db` / `runs`; no secrets logged |
| 21 | Phase D2: `HermesDockerSandboxBackend` | PENDING | container lifecycle vs local non-sensitive repo (no GitHub) |
| 22 | Phase D3: smoke-test repo + GitHub App short token + Draft PR + CI + Reviewer + round-2 | PENDING | no access to `hermes-learning-os` |

---

## Environment facts (this sandbox)

- OS: Windows (Git Bash) — **local control/authoring only**, not the cloud server.
- `gh` authenticated as `yzhlx` (repo scope).
- SSH access to the cloud Ubuntu host EXISTS (user `ubuntu`, key `~/.ssh/id_rsa`,
  passwordless `sudo`); reachable at `129.211.0.213:22`. Dedicated Open SWE
  service user `hermes-swe` (uid 996, system, nologin, no sudo/docker) CREATED —
  the App key is now landed under `/etc/hermes-open-swe-lab/secrets/` owned by it.
- GitHub App key is landed on the server. The LangSmith Service Key was previously
  landed but is now **RETIRED**: its secret values remain in `/opt/hermes-open-swe-lab/.env`
  **only until the user revokes the Key from the web UI**, after which the agent will
  purge the 8 LangSmith vars and the local temp key file (HELD — not yet deleted).
- The cloud server is control-plane only and no longer needs LangSmith.

## What is safe to do now (autonomous)

- Authoring: adapter code, tests, preflight script, smoke-test scaffolding, docs.
- Local verification: unit tests, preflight script structure (no-key graceful path),
  smoke-contract validator against fixtures, **D1 offline test suite**, D1 integration smoke.
- Git: commits on `phase-d0-d1`, push of feature branches (never `main`).

## What requires the authorization gate (Step 9)

- ~~Create the dedicated Open SWE service user~~ — **DONE**: `hermes-swe` (uid 996,
  system, nologin, no sudo/docker) created; App key landed under
  `/etc/hermes-open-swe-lab/secrets/` owned by it. Do NOT reuse Hermes user `ubuntu`.
- ~~Create LangSmith Service Key + land it~~ — **RETIRED** by architecture decision
  (`ADR-002`); do NOT re-enable, do NOT self-upgrade billing, do NOT create snapshots.
  **Remaining gate:** the user must **revoke the LangSmith Service Key from the web UI**;
  only after that confirmation will the agent purge the server `.env` LangSmith vars
  and the local temp key file.
- Write relay Base URL / model / key to `/opt/hermes-open-swe-lab/.env`.
- Any billing/payment action.
- Cloud-server SSH access to run the control plane.

## Last updated

2026-07-25 — **Phase D0 + D1 delivered (docs + offline implementation):**
- `ADR-002-REMOVE-LANGSMITH.md` added: LangSmith removed from MVP-0 (org-level
  Sandboxes disabled; no card to enable). `SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`
  confirmed earlier; no LangSmith feature ever passed.
- `MVP-0-ARCHITECTURE.md` rewritten for the Cloud Control Plane + Local Hermes Docker
  Worker (pull) architecture; includes the Worker API protocol and SQLite schema.
- `SECURITY-BOUNDARIES.md` updated: B7 (custom sandbox backend only), B11 (secret-free
  observability), new B15 (local Worker pull model — no inbound port / no docker.sock
  to cloud / no public SSH).
- `LANGSMITH-SETUP.md` marked `RETIRED_BY_ARCHITECTURE_DECISION` (historical only).
- Phase D1 implemented in `hermes_worker/` (db, protocol, control_plane,
  worker_api_server, worker, echo_sandbox, docker_sandbox stub) + `scripts/` CLIs +
  `tests/test_d1_offline.py`. **7 offline tests PASS**; integration smoke PASS.
- LangSmith secret purge **HELD** pending user revocation of the Service Key.
