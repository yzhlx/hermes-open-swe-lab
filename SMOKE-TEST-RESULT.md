# SMOKE-TEST-RESULT.md — smoke-test current status

**Updated:** 2026-07-25

## What is DONE

| Item | Status | Evidence |
| --- | --- | --- |
| Smoke-test repo created (private) | DONE | `https://github.com/yzhlx/hermes-open-swe-smoke-test` (main seeded) |
| Strict root `AGENTS.md` | DONE | enforces single-repo scope, no main push/merge, no workflow edit, only allowed file, reject secret/host-env reads |
| Deterministic validator | DONE | `scripts/validate_smoke_contract.py` |
| Validator self-test | DONE | 7 scenarios (round-1 + round-2), all assertions hold, exit 0 |
| Validator CI-mode simulation | DONE | temp git repo: complete contract → PASS (exit 0); extra file → FAIL (exit 1) |
| Round-2 label gate (orchestrator) | DONE | `scripts/orchestrate_round2.py`; sole label owner + no-degradation; self-test 7/7 in CI (Run `30147269747`) |
| CI workflow file | DONE | **MERGED to main** via PR #1 (merge `5dba406`); bootstrap self-test Run `30147269747` success; Run `30146077235` retained as initial evidence |

## What is NOT_TESTED (requires authorization + cloud)

| Item | Blocker |
| --- | --- |
| Live Issue → agent → Draft PR loop | GitHub App + cloud control plane |
| Live deterministic CI run on PR #2+ | GitHub App + cloud control plane (to open a real Issue→PR that triggers the workflow) |
| Reviewer feedback → rework → re-review | LangSmith sandbox + reviewer agent |
| End-to-end evidence (trace IDs, PR number) | full live run |

## Evidence excerpts

### Validator self-test (offline)

```bash
$ python scripts/validate_smoke_contract.py --self-test
  [OK] self-test A_round1_baseline_only (round 1): got PASS, expected PASS
  [OK] self-test B_round2_full (round 2): got PASS, expected PASS
  [OK] self-test C_round2_missing_feedback (round 2): got FAIL, expected FAIL
  [OK] self-test D_extra_file (round 1): got FAIL, expected FAIL
  [OK] self-test E_workflow_change (round 1): got FAIL, expected FAIL
  [OK] self-test F_wrong_first_line (round 1): got FAIL, expected FAIL
  [OK] self-test G_missing_baseline (round 1): got FAIL, expected FAIL
Self-test: ALL PASS
```

### Validator CI mode (git diff)

```bash
# round 1: allowed file + BASELINE_AUTOMATION_PASSED only           -> PASS (exit 0)
# round 1: + stray.txt                                             -> FAIL (exit 1, only_allowed_file)
# round 2: allowed file + both markers                             -> PASS (exit 0)
# round 2: BASELINE only (missing SECOND_ROUND_FEEDBACK_APPLIED)    -> FAIL (exit 1, feedback_marker)
```

## PR #1 merge evidence (2026-07-25)

- PR #1 `bootstrap-smoke-ci` → `main`: **MERGED** by `yzhlx` at 2026-07-25T06:46:48Z.
- Merge commit / post-merge `main` SHA: `5dba406887ffd1252551d1952d221d1b4c22346f`.
- Files verified present on `origin/main`: `AGENTS.md`, `README.md`,
  `scripts/validate_smoke_contract.py`, `scripts/orchestrate_round2.py`,
  `.github/workflows/smoke-contract.yml`.
- Bootstrap one-time condition hard-coded to `github.event.pull_request.number == 1`
  (strict path `!= 1`); PR #2+ can never match → strict contract always applies.
- Latest workflow run: `30147269747` (bootstrap self-test) → **success**. No new run
  is triggered by the merge to `main` (workflow triggers on `pull_request` only).
- Bootstrap branch `bootstrap-smoke-ci` still exists (at `669792b`) — left intact.

## Interpreting the two-round result

The contract uses a round model enforced by the validator's `--round` flag:

- **Round 1** (default): the agent creates the allowed file from the Issue with
  the `BASELINE_AUTOMATION_PASSED` marker. CI requires the baseline marker only;
  a round-1 push with baseline only **PASSES**, so the PR can be marked ready for
  review.
- **Round 2**: after the reviewer requests the feedback marker, the agent amends
  the same PR to also add `SECOND_ROUND_FEEDBACK_APPLIED`. CI (round 2) requires
  **both** markers and **fails** if the feedback marker is missing. Only then is
  the two-round contract fully satisfied. `main` is never merged in MVP-0.
