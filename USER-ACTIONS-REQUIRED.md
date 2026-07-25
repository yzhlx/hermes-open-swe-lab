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

**You need to complete:**

1. **GitHub App** (only on the smoke-test repo):
   - create the App, generate the private key (`.pem`), record App ID +
     Installation ID + webhook secret.
   - install on **only** `yzhlx/hermes-open-swe-smoke-test`.
   - → see `GITHUB-APP-SETUP.md`.

2. **LangSmith**: create API key, confirm sandbox permission, build a sandbox
   snapshot from the pinned Open SWE baseline, set `SANDBOX_TYPE=langsmith`.
   - → see `LANGSMITH-SETUP.md`.

3. **Relay credentials**: write `OPEN_SWE_OPENAI_BASE_URL`, `OPEN_SWE_OPENAI_MODEL`,
   and `OPEN_SWE_OPENAI_API_KEY` to `/opt/hermes-open-swe-lab/.env` on the cloud
   server. Never send these in chat.

4. ~~**`workflow` token scope**~~ — **RESOLVED**: the CI workflow was merged to
   `main` via PR #1 (the user merged it manually). No further action needed for
   the workflow file itself.

5. **Cloud server SSH access**: provide/confirm SSH access so the control plane
   can be deployed (Step 10 live run). If you prefer to run the control plane
   yourself, that is also acceptable — the agent only needs the resulting
   evidence.

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
