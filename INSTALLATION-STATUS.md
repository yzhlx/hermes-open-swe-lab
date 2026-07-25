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
| 14 | LangSmith Service Key landing + tracing | BLOCKED | `LANGSMITH_API_KEY` written to server `.env` (hermes-swe 600); read-only auth PASS; exactly 1 workspace `6fa1ef37-…`. **Diagnosis 2026-07-25 (rule C):** official LangSmith CLI v0.2.42 (`langsmith sandbox list`) → exit 1 / HTTP 403; direct API `GET /v2/sandboxes/boxes` → **HTTP 403 identical WITH and WITHOUT `X-Tenant-Id`**, body `detail.error=FeatureDisabled`, `detail.message="Sandbox feature is not enabled for this organization"` (no `error_id` in body). → `STATUS: SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`. NOT a permission/role issue (rule B) and NOT a missing-header 403 (rule D). Prior `SANDBOX_ACCESS_REQUIRED` confirmed as a real feature-disable, not a misjudgment. No snapshot/loop until org-level Sandboxes enablement. |
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
  on: (a) **`STATUS: SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`** for the LangSmith key
  (org-level Sandboxes feature disabled — verified via official CLI v0.2.42 + direct
  API, 403 identical with/without `X-Tenant-Id`), (b) relay creds not yet written.

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
  read-only auth PASS, single workspace `6fa1ef37-…`). **`STATUS:
  SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`** (rule C): Sandboxes feature not enabled for
  this org — `detail.error=FeatureDisabled`, `detail.message="Sandbox feature is not
  enabled for this organization"`. Enable Sandboxes at the LangSmith org/account level
  (product enablement / plan / contact LangChain) — do NOT self-upgrade billing or
  create snapshots; then re-run `langsmith sandbox list` to confirm HTTP 200.
- Write relay Base URL / model / key to `/opt/hermes-open-swe-lab/.env`.
- Any billing/payment action.
- Cloud-server SSH access to run the control plane.

## Last updated

2026-07-25 — LangSmith Service Key landed: `LANGSMITH_API_KEY` written to
`/opt/hermes-open-swe-lab/.env` (hermes-swe 600, merged with GitHub App config,
no dups); read-only auth PASS (workspaces API → exactly 1 workspace
`6fa1ef37-de45-4c81-92d4-810d00d58327` = Workspace 1 scope; orgs boundary OK).
**Diagnosis (rule C): Sandboxes API returns HTTP 403 with explicit
`detail.error=FeatureDisabled` / `detail.message="Sandbox feature is not enabled
for this organization"`** — verified via official LangSmith CLI v0.2.42
(`langsmith sandbox list` → exit 1) AND direct `GET /v2/sandboxes/boxes`
(HTTP 403 identical WITH and WITHOUT `X-Tenant-Id`). →
`STATUS: SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`. Prior `SANDBOX_ACCESS_REQUIRED`
is now refined, NOT a misjudgment. No snapshot/loop until org-level enablement.
Webhook OFF. GitHub App fully landed earlier (svc user `hermes-swe`, key at
`/etc/.../secrets/`, SHA MATCH).
