# SMOKE-TEST-PLAN.md — deterministic two-round smoke test

**Target repo:** `yzhlx/hermes-open-swe-smoke-test` (strict, private)
**Validator:** `scripts/validate_smoke_contract.py` (in the smoke-test repo)
**Spec:** MVP-0 Step 8

## The scenario

The Open SWE coding agent must prove it can:
1. read an Issue,
2. create **exactly one** file,
3. pass the deterministic CI contract,
4. respond to reviewer feedback by amending the **same** Draft PR.

## Two rounds

**Round 1 — from Issue.**
The agent creates `automation-smoke-test/README.md`:

```markdown
# Hermes Open SWE Smoke Test

BASELINE_AUTOMATION_PASSED

<brief description of what this smoke test validates>
```

**Round 2 — after reviewer feedback.**
The independent reviewer comments requesting a confirmation marker. The agent
amends the same PR's branch to also include:

```markdown
SECOND_ROUND_FEEDBACK_APPLIED

<brief note that the reviewer's feedback was addressed>
```

## The contract (CI-enforced, deterministic)

| Check | Requirement |
| --- | --- |
| File exists | `automation-smoke-test/README.md` |
| Only that file changed | no other file added/modified/deleted |
| No workflow change | nothing under `.github/workflows/` |
| First line | `# Hermes Open SWE Smoke Test` |
| Baseline marker | `BASELINE_AUTOMATION_PASSED` |
| Feedback marker | `SECOND_ROUND_FEEDBACK_APPLIED` (round 2 only) |

Round model:

- **Round 1** (default): the agent creates the file from the Issue. CI requires
  the baseline marker only; the feedback marker is **not** forced yet. A plain
  Draft PR therefore passes before reviewer feedback. No PR label is required.
- **Round 2**: after the reviewer requests the feedback marker, the agent amends
  the same PR. CI enforces **both** markers. In the smoke-test workflow this is
  triggered by the PR label `round-2`.

### Round-2 label gate (who adds the label, and the no-degradation rule)

The second round is keyed by exactly one PR label: `round-2`.

1. **Sole responsible party:** the **test orchestrator** (control-plane process)
   is the only party allowed to add `round-2`. The coding agent and CI never add
   it.
2. **Confirm before proceeding:** the orchestrator adds `round-2` to the original
   PR and reads the labels back to confirm presence. Only after confirmation does
   it post the rework comment.
3. **Canonical sequence (fixed, do not reorder):**
   1. Round 1 CI PASS
   2. Independent reviewer completes review
   3. Orchestrator adds the `round-2` label to the original PR
   4. Orchestrator confirms the label is present (read-back)
   5. Orchestrator posts the rework comment
   6. Agent commits the second commit (adds `SECOND_ROUND_FEEDBACK_APPLIED`)
   7. CI runs with `--round 2`
   8. CI forces verification of `SECOND_ROUND_FEEDBACK_APPLIED`
   9. Independent reviewer re-reviews
4. **No silent degradation (hard rule):** once the flow has entered
   `FEEDBACK_REQUESTED` / `REWORK_RUNNING`, if the label add FAILED, the label
   READ FAILED, or the label is ABSENT:
   - stop round 2 immediately;
   - do **not** fall back to round 1;
   - emit an explicit failure (`ROUND_2_LABEL_GATE_FAILED`);
   - do **not** report "round 2 CI PASS".

Round 1 still passes with **no label** (default round 1). The gate logic is in
`scripts/orchestrate_round2.py` (smoke-test repo) and is self-tested.

### Deterministic verification (requirement 6)

Both self-tests run in CI on PR #1 and locally:

| # | Requirement | Proven by |
| --- | --- | --- |
| 1 | no `round-2` label → round 1 | orchestrator `no_label_round1_defaults_round1` |
| 2 | `round-2` label present → round 2 | orchestrator `has_label_round2_confirmed` |
| 3 | round 2 missing `SECOND_ROUND_FEEDBACK_APPLIED` → FAIL | validator `C_round2_missing_feedback` |
| 4 | round 2 contains marker → PASS | validator `B_round2_full` (+ orchestrator `round2_full_content_pass`) |
| 5 | entered round 2 but label absent/add-/read-fail → STOP, no degrade | orchestrator `add_fails_stops_no_degrade`, `read_fails_stops_no_degrade`, `label_absent_after_add_stops` |

Run locally or in CI:

```bash
python scripts/validate_smoke_contract.py --self-test                 # offline
python scripts/validate_smoke_contract.py --root . --base B --head H  # CI (round 1)
python scripts/validate_smoke_contract.py --root . --base B --head H --round 2  # CI (round 2)
```

The CI workflow (`.github/workflows/smoke-contract.yml`) runs on
`pull_request`, with `timeout-minutes: 2` and least-privilege permissions.

## Allowed vs forbidden (agent)

- Allowed write: only `automation-smoke-test/README.md`.
- Forbidden: any other file, any `.github/workflows/` edit, pushing `main`,
  merging, reading secrets or host env, accessing other repos.

## Expected evidence (after live run)

- Draft PR on smoke-test repo adding the allowed file (round 1).
- CI contract check **PASS** (both markers present at final state).
- Reviewer comment present (feedback request).
- Second commit on the same PR/branch adding the feedback marker (round 2).
- Reviewer re-review on the new head SHA.
- `main` unchanged; no merge.

## Current state (as of 2026-07-25)

- The CI workflow (`.github/workflows/smoke-contract.yml`) and validator
  (`scripts/validate_smoke_contract.py`) are now **pushed** to the smoke-test
  repo on branch `bootstrap-smoke-ci` (PR #1) via SSH, bypassing the missing
  `workflow` PAT scope. The contract is no longer enforced only manually.
- PR #1 runs the one-time **bootstrap self-test** path: it exercises both
  `validate_smoke_contract.py --self-test` and `orchestrate_round2.py --self-test`
  and confirms the required files exist. It does NOT relax the strict contract
  for any later PR.
- The strict contract (single-file, two-round, no workflow change) activates for
  every PR opened after PR #1 is merged (PR #2, #3, …).
