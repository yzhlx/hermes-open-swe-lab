# USER-ACTIONS-REQUIRED.md — authorizations the user must complete

**When to read this:** the agent stops here and emits `STATUS: USER_ACTION_REQUIRED`
before any live testing (MVP-0 Step 9). The agent does **not** ask for ordinary
logs, does not ask you to relay messages between agents, and never asks for
secrets in chat.

---

## STATUS: USER_ACTION_REQUIRED

**Current progress (autonomous prep complete):**
- Root `AGENTS.md` verified strict; guardrails committed (`8572f03`).
- Read-only audit done; 4 audit docs generated.
- Relay adapter + 17 offline unit tests PASS.
- Provider preflight script authored (no-config run → `NOT_TESTED`, exit 2).
- Smoke-test repo `yzhlx/hermes-open-swe-smoke-test` created (private) and
  seeded with strict `AGENTS.md`, validator, README.
- **Smoke-test contract system MERGED to main (PR #1, merge `5dba406`):**
  validator `scripts/validate_smoke_contract.py`, round-2 label-gate
  orchestrator `scripts/orchestrate_round2.py`, and CI workflow
  `.github/workflows/smoke-contract.yml` are all live on `main`; bootstrap
  self-test Run `30147269747` passed. Strict contract (round 1 baseline, round 2
  + feedback marker, round-2 label gate) verified.
- MVP-0 documentation set complete.
- **GitHub App DONE**: App `4389778` created + installed on smoke-test only;
  Installation ID `148886992` obtained via read-only App auth; install scope
  verified (lab + learning-os both 404, no `SECURITY_BOUNDARY_VIOLATION`).
  Webhook intentionally OFF. Key-file adapter added (`scripts/load_github_app_key.py`).
- **BLOCKER**: the dedicated Open SWE service user does not exist on the cloud
  server, so the App private key cannot yet be landed (`/etc/hermes-open-swe-lab/secrets/`).
  Must NOT reuse the Hermes service user `ubuntu`.

**Immediate next action (blocking):**

1. **Create the dedicated Open SWE service user on the cloud server.**
   - The existing Hermes runtime runs as `ubuntu` (and partially `root`); per the
     security rule we must NOT reuse `ubuntu` as the GitHub App key-file owner.
   - Proposed name: `hermes-swe`. On the cloud host (`129.211.0.213:22`, reachable
     as `ubuntu` with passwordless sudo):
     ```bash
     sudo useradd --system --create-home --shell /usr/sbin/nologin hermes-swe
     ```
   - Reply with the username you created (or confirm `hermes-swe`). The agent then
     lands the App private key under `/etc/hermes-open-swe-lab/secrets/` (700/600,
     owned by that user) and writes the non-secret `/opt/hermes-open-swe-lab/.env`.

**Still required before the live loop (Step 10):**

2. **LangSmith**: create API key, confirm sandbox permission, build a sandbox
   snapshot from the pinned Open SWE baseline, set `SANDBOX_TYPE=langsmith`.
   - → see `LANGSMITH-SETUP.md`.

3. **Relay credentials**: write `OPEN_SWE_OPENAI_BASE_URL`, `OPEN_SWE_OPENAI_MODEL`,
   and `OPEN_SWE_OPENAI_API_KEY` to `/opt/hermes-open-swe-lab/.env` on the cloud
   server. Never send these in chat.

4. ~~**`workflow` token scope**~~ — **RESOLVED**: the CI workflow was merged to
   `main` via PR #1 (the user merged it manually). No further action needed for
   the workflow file itself.

5. ~~**Cloud server SSH access**~~ — **CONFIRMED**: SSH to `129.211.0.213:22` as
   `ubuntu` (key `~/.ssh/id_rsa`, passwordless sudo) works from this sandbox. The
   control-plane deploy now only waits on the service user (above) + LangSmith +
   relay creds. If you prefer to run the control plane yourself, that is also
   acceptable — the agent only needs the resulting evidence.

6. **Billing/payment**: if any provider, LangSmith, or sandbox incurs cost,
   approve it explicitly. The agent will stop before any charge.

**Do not send these values in chat, an Issue, or a PR:**
- API keys
- GitHub App private key
- Webhook secret
- LangSmith key
- relay key

**Write secrets directly to:** `/opt/hermes-open-swe-lab/.env` (server-side,
gitignored).

**After completing the actions, reply only:**
```
已完成授权
```
The agent will then proceed with the live test sequence (Step 10) and the Draft
PR delivery (Step 12).
