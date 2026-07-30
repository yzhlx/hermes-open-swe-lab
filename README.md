# Hermes Open SWE Lab

Isolated engineering-automation laboratory for evaluating and adapting
[Open SWE](https://github.com/langchain-ai/open-swe) into the future
Hermes Engineering Automation system.

> This is **not** the production Hermes Learning OS repository.
> The production repository `yzhlx/hermes-learning-os` is protected and must
> never be accessed from this lab. See [`AGENTS.md`](./AGENTS.md) for the full
> constraint hierarchy and security boundaries.

## Current milestone: MVP-0

End-to-end closed loop:

```text
GitHub Issue
  → Open SWE coding agent
  → isolated local Docker sandbox (LangSmith retired — see ADR-002)
  → draft pull request (on smoke-test repo)
  → independent code reviewer
  → PR comment feedback
  → original agent continues on the same PR
  → reviewer re-reviews new head
  → auditable evidence
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Root guardrails (mandatory, authority over all other docs) |
| `hermes_open_swe_relay/` | Optional OpenAI-compatible relay adapter (opt-in) |
| `tests/` | Offline unit tests for the relay adapter |
| `scripts/provider_preflight.py` | Provider compatibility preflight (P1–P6) |
| `docs/` | MVP-0 architecture, audit, security, runbook, rollback |

## Approved upstream baseline

```text
Repository: langchain-ai/open-swe
Commit:     ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6
```

Do **not** follow a floating `main`. Every upstream change uses an explicit
commit SHA, a dedicated branch, a compare report, and a PR that stays unmerged
until approved.

## Status

MVP-0 implementation, documentation, and offline test scaffolding are in
progress on the `phase-d0-d1` branch. D0 (LangSmith removal), D1 (cloud
control plane + local worker protocol, offline), and D2 (local Docker sandbox
backend, real-daemon validated) are complete. Live verification (GitHub App
webhook, real Issue→PR loop, reviewer loop) is tracked under `NOT_TESTED`
until D3 authorizations are granted.

See [`USER-ACTIONS-REQUIRED.md`](./USER-ACTIONS-REQUIRED.md) for the
list of user-authorization steps that must be completed before live testing.
