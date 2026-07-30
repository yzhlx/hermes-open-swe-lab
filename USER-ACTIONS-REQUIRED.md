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
- **GitHub App DONE (incl. server landing)**: App `4389778` created + installed on
  smoke-test only; Installation ID `148886992` obtained via read-only App auth;
  install scope verified (lab + learning-os not in scope). Dedicated svc user
  `hermes-swe` (uid 996, system, nologin, no sudo/docker) created; App key landed at
  `/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem` (hermes-swe:hermes-swe
  600, SHA-256 MATCH vs local); `/opt/hermes-open-swe-lab/.env` written (non-secret
  only); server-side read-only auth verify PASS (allowed repo list == smoke-test only).
  Webhook intentionally OFF. Key-file adapter added (`scripts/load_github_app_key.py`).

- **LangSmith Service Key landed (auth PASS, Sandboxes BLOCKED)**: key
  `LANGSMITH_API_KEY` written to server `.env` (hermes-swe 600, merged with the
  GitHub App config, no duplicate keys); read-only auth PASS — `GET /api/v1/workspaces`
  returned **exactly 1 workspace** `6fa1ef37-de45-4c81-92d4-810d00d58327` (= Workspace 1
  scope). **BLOCKER: the Sandboxes API returned HTTP 403** on `GET /v2/sandboxes/boxes`
  and `GET /v2/sandboxes` → `STATUS: SANDBOX_ACCESS_REQUIRED`. No snapshot or live loop
  until Sandboxes permission is granted. Webhook OFF.

**GitHub App service user + key landing: DONE.** `hermes-swe` created; key landed;
`.env` written; server-side auth verify PASS. (Recorded in `GITHUB-APP-SETUP.md`.)

**Still required before the live loop (Step 10):**

2. **LangSmith Sandboxes permission — BLOCKER (`STATUS: SANDBOX_ACCESS_REQUIRED`)**:
   the Service Key is created + landed and read-only auth PASSED, but the Sandboxes
   API returned **HTTP 403**. You must enable Sandboxes access for this key/workspace
   (the LangSmith plan tier or workspace setting that grants the `sandboxes` scope),
   then reply `已完成授权`. After permission, the agent builds the snapshot from the
   pinned baseline (`scripts/create_sandbox_snapshot.py`, image
   `johanneslangchain/open-swe-sandbox:gh-cli-amd64`) and sets
   `DEFAULT_SANDBOX_SNAPSHOT_ID`; tracing is already configured
   (`SANDBOX_TYPE=langsmith`, `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_PROJECT=open-swe-agent`).
   - → see `LANGSMITH-SETUP.md`.

3. **Relay credentials**: write `OPEN_SWE_OPENAI_BASE_URL`, `OPEN_SWE_OPENAI_MODEL`,
   and `OPEN_SWE_OPENAI_API_KEY` to `/opt/hermes-open-swe-lab/.env` on the cloud
   server. Never send these in chat.

4. ~~**`workflow` token scope**~~ — **RESOLVED**: the CI workflow was merged to
   `main` via PR #1 (the user merged it manually). No further action needed for
   the workflow file itself.

5. ~~**Cloud server SSH access**~~ — **CONFIRMED**: SSH to `129.211.0.213:22` as
   `ubuntu` (key `~/.ssh/id_rsa`, passwordless sudo) works from this sandbox; the
   dedicated `hermes-swe` service user is now created and the App key is landed.
   The control-plane deploy now waits on LangSmith + relay creds. If you prefer to
   run the control plane yourself, that is also acceptable — the agent only needs
   the resulting evidence.

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
