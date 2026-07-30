# OPEN-SWE-PHASE-1-RESULT.md — MVP-0 Phase-1 outcome

**Date:** 2026-07-25 · **Branch:** `phase-1-smoke` · **Baseline:** `ed12bb8…`

## Verdict

> **Autonomous preparation: COMPLETE.** Smoke-test contract system **MERGED to
> main** (PR #1, merge `5dba406`); deterministic CI contract is live and its
> self-tests pass in CI (Run `30147269747`). Live end-to-end verification:
> **NOT_TESTED** (blocked by the authorization gate, Step 9 — GitHub App,
> LangSmith, relay creds, cloud server). Cannot declare `PHASE_1_PASS` until
> live evidence exists. **Go/No-Go: GO to proceed to live once the user
> completes USER-ACTIONS-REQUIRED (next node: GitHub App).**

## Definition-of-done checklist (AGENTS.md Section 16)

| Required check | Status | Evidence |
| --- | --- | --- |
| Upstream baseline pinned | DONE | `UPSTREAM-BASELINE.md`; SHA reachable via `gh api` |
| Relay multi-turn tool calling verified | NOT_TESTED (live) | adapter tool-call passthrough unit-tested; live P2/P3 pending creds |
| GitHub webhook verified | NOT_TESTED | App + webhook pending user (GITHUB-APP-SETUP) |
| Read-only task verified w/o writes | NOT_TESTED | live run pending |
| Isolated coding task verified | NOT_TESTED | LangSmith sandbox pending |
| Draft PR created (smoke-test) | NOT_TESTED | live loop pending |
| Deterministic CI passed | DONE | validator + orchestrator self-tests PASS in CI (Run `30147269747`); contract live on `main` |
| Independent reviewer executed | NOT_TESTED | reviewer agent pending |
| PR feedback resumed coding | NOT_TESTED | live loop pending |
| Second commit on original PR | NOT_TESTED | live loop pending |
| `main` remained unchanged | DONE | all work on `phase-1-smoke`; never touched `main` |
| No automatic merge | DONE | no auto-merge configured; merges are user-only |
| Protected Hermes not accessed | DONE | only protective mentions; no clone/fetch/op |
| Server remained healthy | NOT_TESTED | live run pending |
| Trace + final docs exist | PARTIAL | docs exist; live traces NOT_TESTED |

## What was delivered autonomously

- Repository guardrails (verified strict `AGENTS.md` + `.gitignore` + README).
- Read-only audit (4 docs).
- Relay adapter + 17 offline tests PASS.
- Provider preflight script (graceful `NOT_TESTED` without creds).
- Smoke-test repo created (private) + strict contract + verified validator.
- **Phase D0/D1: cloud control plane + local Worker + `EchoSandboxBackend`** —
  7 offline tests PASS.
- **Phase D2: `HermesDockerSandboxBackend`** — 11 offline tests PASS; **D2.5 real
  Docker daemon validation PASS** (2026-07-25): full container lifecycle against
  the user's local Docker Desktop (Engine 29.2.1), `docker inspect` proves REAL
  limits (cpus=1, mem=2GB, pids=256, privileged=false, net=bridge, auto-remove,
  single workdir mount), local bare-remote push received commit `c974e59`, 0
  residual containers, no secret in logs. Real-test-driven fix: `_safe_path()`
  workspace-containment guard (+ regression test). D1 7/7 + D2 offline 11/11 +
  D2 real smoke all PASS.
- Full MVP-0 documentation set (16+ docs).

## Blockers to `PHASE_1_PASS`

1. Authorization gate (GitHub App, LangSmith, relay creds) — user action.
   **Next node: create + install the GitHub App on `yzhlx/hermes-open-swe-smoke-test`.**
2. Cloud-server access to run the control plane — user action / user-run.

## Merge evidence (2026-07-25)

- PR #1 merged to `main` by `yzhlx` (merge commit / `main` SHA
  `5dba406887ffd1252551d1952d221d1b4c22346f`).
- Contract system live on `main`: validator, round-2 label-gate orchestrator,
  CI workflow. Bootstrap self-test Run `30147269747` → success.
- Strict contract unchanged for PR #2+ (bootstrap condition hard-coded to PR #1).

## Recommended next step

User completes the **GitHub App** action in USER-ACTIONS-REQUIRED (create + install
the App on `yzhlx/hermes-open-swe-smoke-test` with `pull_requests: read` + `write`),
then replies "已完成授权". Agent proceeds with the live test sequence (Step 10) and
Draft PR delivery (Step 12) → re-evaluate this table → declare `PHASE_1_PASS` or
`PHASE_1_FAILED` with evidence.
