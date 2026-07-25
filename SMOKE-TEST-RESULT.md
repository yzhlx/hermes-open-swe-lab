# SMOKE-TEST-RESULT.md — smoke-test current status

**Updated:** 2026-07-25

## What is DONE

| Item | Status | Evidence |
| --- | --- | --- |
| Smoke-test repo created (private) | DONE | `https://github.com/yzhlx/hermes-open-swe-smoke-test` (main seeded) |
| Strict root `AGENTS.md` | DONE | enforces single-repo scope, no main push/merge, no workflow edit, only allowed file, reject secret/host-env reads |
| Deterministic validator | DONE | `scripts/validate_smoke_contract.py` |
| Validator self-test | DONE | 5 scenarios, all assertions hold, exit 0 |
| Validator CI-mode simulation | DONE | temp git repo: complete contract → PASS (exit 0); extra file → FAIL (exit 1) |
| CI workflow file | STAGED-LOCAL | present on disk; **not pushed** (missing `workflow` token scope) |

## What is NOT_TESTED (requires authorization + cloud)

| Item | Blocker |
| --- | --- |
| Live Issue → agent → Draft PR loop | GitHub App + cloud control plane |
| Live deterministic CI run | CI workflow push (workflow scope) |
| Reviewer feedback → rework → re-review | LangSmith sandbox + reviewer agent |
| End-to-end evidence (trace IDs, PR number) | full live run |

## Evidence excerpts

### Validator self-test (offline)

```bash
$ python scripts/validate_smoke_contract.py --self-test
  [OK] self-test A_complete: got PASS, expected PASS
  [OK] self-test B_round1_only: got FAIL, expected FAIL
  [OK] self-test C_extra_file: got FAIL, expected FAIL
  [OK] self-test D_workflow_change: got FAIL, expected FAIL
  [OK] self-test E_wrong_first_line: got FAIL, expected FAIL
Self-test: ALL PASS
```

### Validator CI mode (git diff)

```bash
# only allowed file + both markers  -> PASS (exit 0)
# + stray.txt                       -> FAIL (exit 1, only_allowed_file)
```

## Interpreting the two-round result

An incomplete round-1 push (baseline marker only, no feedback marker) correctly
**fails** the contract — this is the gate working as designed. The contract is
satisfied only after round 2 adds the feedback marker, at which point CI PASSes
and the PR can be marked ready for review. `main` is never merged in MVP-0.
