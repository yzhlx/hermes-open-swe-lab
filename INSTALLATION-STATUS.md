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
| 13 | GitHub App + webhook | NOT_TESTED | **next authorization node** (Step 9) |
| 14 | LangSmith sandbox + trace | NOT_TESTED | user gate (Step 9) |
| 15 | Cloud control plane deploy | NOT_TESTED | needs cloud-server access |
| 16 | End-to-end loop (Issue→PR→review→rework) | NOT_TESTED | depends on 12–15 |
| 17 | Draft PR to lab repo | PENDING | Step 12 — after live loop |
| 18 | Cloud server health monitor | NOT_TESTED | needs cloud-server access |

---

## Environment facts (this sandbox)

- OS: Windows (Git Bash) — **local control/authoring only**, not the cloud server.
- `gh` authenticated as `yzhlx` (repo scope).
- No SSH/credential to the cloud Ubuntu host from this sandbox.
- No real relay API key / GitHub App / LangSmith key present → live runs blocked
  by design until authorization.

## What is safe to do now (autonomous)

- Authoring: adapter code, tests, preflight script, smoke-test scaffolding, docs.
- Local verification: unit tests, preflight script structure (no-key graceful path),
  smoke-contract validator against fixtures.
- Git: commits on `phase-1-smoke`, push of feature branches (never `main`).

## What requires the authorization gate (Step 9)

- Create GitHub App (private key, App ID, Installation ID, webhook secret).
- Create LangSmith API key + confirm sandbox permission + create snapshot.
- Write relay Base URL / model / key to `/opt/hermes-open-swe-lab/.env`.
- Any billing/payment action.
- Cloud-server SSH access to run the control plane.

## Last updated

2026-07-25 — smoke-test contract system MERGED to main (PR #1, merge `5dba406`):
validator + round-2 label-gate orchestrator + CI workflow live on main. Relay
adapter + provider preflight DONE. **GitHub App is the next authorization node.**
