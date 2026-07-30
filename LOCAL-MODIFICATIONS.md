# LOCAL-MODIFICATIONS.md — what this lab repo changes vs the approved baseline

**Principle:** we do **not** vendor or modify `langchain-ai/open-swe` source in
this repo. We pin the baseline and build the *adaptation layer* around it.

## Relationship to upstream

| Aspect | Decision |
| --- | --- |
| Upstream source | NOT copied into this repo |
| Upstream baseline | pinned SHA `ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6` (documented, not floated) |
| Upstream updates | dedicated branch + compare report + PR, unmerged until approved |
| Integration | the control plane checks out the pinned SHA inside a LangSmith sandbox |

## Files added in this repo (MVP-0)

| Path | Purpose | Commit |
| --- | --- | --- |
| `.gitignore` | enforce Section 7 secret-ignore patterns | `8572f03` |
| `README.md` | repo overview (rewritten UTF-8) | `8572f03` |
| `hermes_open_swe_relay/` | opt-in OpenAI-compatible relay adapter | (pending commit on `phase-1-smoke`) |
| `tests/` | offline unit tests for the adapter | (pending commit) |
| `pyproject.toml` | packaging + pytest config | (pending commit) |
| `.env.example` | placeholder env (no real secrets) | (pending commit) |
| `scripts/provider_preflight.py` | P1–P6 provider preflight | (pending commit) |
| `INITIAL-AUDIT.md`, `UPSTREAM-BASELINE.md`, `SECURITY-BOUNDARIES.md`, `INSTALLATION-STATUS.md` | audit docs | (pending commit) |
| `MVP-0-ARCHITECTURE.md`, `PROVIDER-ADAPTER.md`, `PROVIDER-PREFLIGHT-RESULT.md`, `GITHUB-APP-SETUP.md`, `LANGSMITH-SETUP.md`, `SMOKE-TEST-PLAN.md`, `SMOKE-TEST-RESULT.md`, `INSTALLATION-PLAN.md`, `LOCAL-MODIFICATIONS.md`, `USER-ACTIONS-REQUIRED.md`, `OPEN-SWE-PHASE-1-RESULT.md`, `OPERATIONS-RUNBOOK.md`, `ROLLBACK-PLAN.md` | MVP-0 documentation set | (pending commit) |

## What we deliberately did NOT do (scope discipline)

- No Feishu integration, no cloud Hermes Master, no local Hermes, no local Codex.
- No Computer Use, Playwright, Temporal, production deploy, multi-agent parallel,
  automatic merge, or mobile adaptation (all out of MVP-0 scope).
- No modification of `yzhlx/hermes-learning-os` or the existing cloud Hermes
  runtime.

## Pending local commit

The adapter, tests, preflight script, and documentation set are staged on the
`phase-1-smoke` branch and will be committed as a single implementation commit
before the Draft PR is opened (Step 12, after the authorization gate).
