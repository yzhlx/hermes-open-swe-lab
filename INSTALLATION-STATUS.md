# INSTALLATION-STATUS.md — Hermes Open SWE Lab (MVP-0)

**Purpose:** Track the implementation/installation status of every MVP-0 component.
Updated as work progresses. Items that cannot be verified in this sandbox are
marked `NOT_TESTED` and tied to the authorization gate (Step 9) or cloud-server access.

Legend: `DONE` · `IN_PROGRESS` · `PENDING` · `NOT_TESTED` (needs auth/cloud)

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
| 7 | Security boundaries doc | DONE | `SECURITY-BOUNDARIES.md` |
| 8 | Smoke-test repo `yzhlx/hermes-open-swe-smoke-test` | DONE | private; contract system MERGED to main (PR #1, merge `5dba406`) |
| 9 | Relay adapter (opt-in) | DONE | `hermes_open_swe_relay/`; 17 offline unit tests PASS |
| 10 | Relay adapter unit tests | DONE | 17 tests PASS |
| 11 | `scripts/provider_preflight.py` | DONE | P1–P6; no-config run → NOT_TESTED (exit 2) |
| 12 | Live provider preflight (P1–P6) | NOT_TESTED | needs relay creds (Step 9) |
| 13 | GitHub App (create + install + scope) | DONE | App `4389778`; installed on smoke-test only; Install ID `148886992` (read-only App auth); scope verified (lab+learning-os 404) |
| 13b | GitHub App key landing on server | DONE | `hermes-swe` svc user created; key at `/etc/.../secrets/github-app-private-key.pem` (hermes-swe:hermes-swe 600); SHA-256 MATCH; `.env` written; server-side auth verify PASS |
| 13c | Webhook | OFF | intentionally disabled in MVP; no secret/URL configured |
| 14 | LangSmith Service Key landing + tracing | PARTIAL | `LANGSMITH_API_KEY` written to server `.env` (hermes-swe 600); read-only auth PASS; exactly 1 workspace `6fa1ef37-…` (Workspace 1 scope); **BLOCKER: Sandboxes API returns HTTP 403** → `STATUS: SANDBOX_ACCESS_REQUIRED` (no snapshot/loop until granted); `LANGSMITH_TENANT_ID_PROD` intentionally unset (orgs endpoint not reliably resolvable for this service key — code tolerates absence; trace URL optional) |
| 15 | Cloud control plane deploy | NOT_TESTED | needs cloud-server access |
| 16 | End-to-end loop (Issue→PR→review→rework) | NOT_TESTED | depends on 12–15 |
| 17 | Draft PR to lab repo | PENDING | Step 12 — after live loop |
| 18 | Cloud server health monitor | NOT_TESTED | needs cloud-server access |

---

## Environment facts (this sandbox)

- OS: Windows (Git Bash) — **local control/authoring only**, not the cloud server.
- `gh` authenticated as `yzhlx` (repo scope).
- SSH access to the cloud Ubuntu host EXISTS (user `ubuntu`, key `~/.ssh/id_rsa`,
  passwordless `sudo`); reachable at `129.211.0.213:22`. Dedicated Open SWE
  service user `hermes-swe` (uid 996, system, nologin, no sudo/docker) CREATED —
  the App key is now landed under `/etc/hermes-open-swe-lab/secrets/` owned by it.
- GitHub App key + LangSmith Service Key are NOW landed on the server (non-secret
  `.env` at `/opt/hermes-open-swe-lab/.env`, hermes-swe 600); live runs still blocked
  on: (a) **Sandboxes API 403** for the LangSmith key, (b) relay creds not yet written.

## What is safe to do now (autonomous)

- Authoring: adapter code, tests, preflight script, smoke-test scaffolding, docs.
- Local verification: unit tests, preflight script structure (no-key graceful path),
  smoke-contract validator against fixtures.
- Git: commits on `phase-1-smoke`, push of feature branches (never `main`).

## What requires the authorization gate (Step 9)

- ~~Create the dedicated Open SWE service user~~ — **DONE**: `hermes-swe` (uid 996,
  system, nologin, no sudo/docker) created; App key landed under
  `/etc/hermes-open-swe-lab/secrets/` owned by it. Do NOT reuse Hermes user `ubuntu`.
- ~~Create LangSmith Service Key + land it~~ — **DONE** (credential landed,
  read-only auth PASS, single workspace `6fa1ef37-…`). **BLOCKER: Sandboxes API
  HTTP 403** for this key — grant Sandboxes permission (LangSmith plan / workspace
  setting), then create the sandbox snapshot (`scripts/create_sandbox_snapshot.py`).
- Write relay Base URL / model / key to `/opt/hermes-open-swe-lab/.env`.
- Any billing/payment action.
- Cloud-server SSH access to run the control plane.

## Last updated

2026-07-25 — LangSmith Service Key landed: `LANGSMITH_API_KEY` written to
`/opt/hermes-open-swe-lab/.env` (hermes-swe 600, merged with GitHub App config,
no dups); read-only auth PASS (workspaces API → exactly 1 workspace
`6fa1ef37-de45-4c81-92d4-810d00d58327` = Workspace 1 scope; orgs boundary OK).
**BLOCKER: Sandboxes API returns HTTP 403** → `STATUS: SANDBOX_ACCESS_REQUIRED`;
no snapshot/loop until permission granted. Webhook OFF. GitHub App fully landed
earlier (svc user `hermes-swe`, key at `/etc/.../secrets/`, SHA MATCH).
