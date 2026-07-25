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
  Draft PR therefore passes before reviewer feedback.
- **Round 2**: after the reviewer requests the feedback marker, the agent amends
  the same PR. CI enforces **both** markers. In the smoke-test workflow this is
  triggered by adding the PR label `round-2` (graceful default to round 1 if the
  label or `gh` is unavailable).

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

## Known caveat

The CI workflow file could not be pushed from this sandbox because the current
`gh` token lacks the `workflow` scope (GitHub blocks workflow-file pushes
without it). The file is present on disk in the smoke-test repo and must be
pushed after `gh auth refresh -s workflow` (see USER-ACTIONS-REQUIRED). Until
then, the contract is enforced manually via the validator's `--self-test` and
CI-mode simulation, which both PASS.
