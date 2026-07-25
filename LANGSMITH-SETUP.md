# LANGSMITH-SETUP.md — LangSmith sandbox & trace for MVP-0

> ## ⚠️ RETIRED_BY_ARCHITECTURE_DECISION
> LangSmith has been **removed** from MVP-0 (see `ADR-002-REMOVE-LANGSMITH.md`,
> 2026-07-25). The org-level Sandboxes feature was disabled
> (`SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED`) and the user has no payment card to enable
> it, so the project must not depend on a card-gated execution environment. This file
> is kept **only as historical evidence** — do not follow these steps. The replacement
> is the local `HermesDockerSandboxBackend` + SQLite Event Store; see
> `MVP-0-ARCHITECTURE.md`.
> **No LangSmith feature was ever verified as "passed" in MVP-0.**

**(Historical) Status:** USER ACTION REQUIRED · NOT_TESTED
**(Historical) Rule:** `SANDBOX_TYPE=langsmith` was mandatory; `SANDBOX_TYPE=local` forbidden.

> Instructions for the user. The agent never creates the LangSmith account,
> holds the API key, or provisions the sandbox. Do **not** paste the key into
> chat, an Issue, or a PR.

## Steps (user)

1. Create/log in to LangSmith (or the LangChain Platform org used by Hermes).
2. Create a **project** for MVP-0 (e.g. `hermes-open-swe-mvp0`).
3. Generate an **API key**; write it to `/opt/hermes-open-swe-lab/.env` as
   `LANGSMITH_API_KEY=<key>`. Enable tracing for the project.
4. Confirm **Sandbox** permission for the org (LangSmith Sandbox / LangGraph
   Platform sandbox capability). If unavailable, stop and report
   `STATUS: SANDBOX_ACCESS_REQUIRED`.
5. Build a **sandbox snapshot** that contains:
   - the pinned Open SWE baseline (`ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6`);
   - the relay adapter env (injected at runtime, not baked in);
   - minimal tooling to run the coding agent and the validator.
   Record the snapshot/ID as `LANGSMITH_SANDBOX_SNAPSHOT=<id>`.
6. Set, on the control plane:

   ```bash
   SANDBOX_TYPE=langsmith
   LANGSMITH_API_KEY=<key>
   LANGSMITH_SANDBOX_SNAPSHOT=<id>
   LANGSMITH_PROJECT=hermes-open-swe-mvp0
   ```

## Sandbox isolation requirements (from AGENTS.md Section 11)

- Receives **only** the minimum repo-scoped credentials for the smoke-test repo.
- Must **not** see host environment variables, full GitHub App private keys,
  credentials for unrelated repositories, or production Hermes credentials.
- Recommended limits: 2 vCPU / 4 GB / 32 GB disk / 1 concurrent sandbox /
  10-min idle timeout / short deletion window.
- Sandbox failures may be retried once; after the retry, stop and preserve
  evidence (`sandbox_attempts=2`).

## Trace & audit

- Every coding-agent and reviewer action emits a LangSmith trace.
- The final evidence report references trace IDs and sandbox IDs.
- Traces must never contain `Authorization` headers or raw keys (the adapter
  redacts them).

## Verification (after user completes)

- The control plane can spawn a sandbox from the snapshot.
- A test run writes a trace to the MVP-0 project.
- No host env or unrelated credential is visible inside the sandbox.
