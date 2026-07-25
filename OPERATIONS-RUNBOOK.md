# OPERATIONS-RUNBOOK.md — running the MVP-0 loop

For the operator (user or delegated control-plane process) once authorization is
complete. All actions are on `phase-1-smoke` / the smoke-test repo; `main` is
never merged automatically.

## Daily operation

1. **Health pre-check** on the cloud server:
   - free memory > headroom under 4 GB (Hermes baseline ~1.51 GB must stay safe);
   - control-plane process up; webhook receiving pings.
2. **Trigger** a run by opening a GitHub Issue on `yzhlx/hermes-open-swe-smoke-test`
   describing the required file change.
3. **Observe** via LangSmith traces + the smoke-test repo PR/Checks.
4. **Collect evidence**: PR number, check-run result, trace IDs, head SHAs,
   changed-file list.

## Test sequence (must not skip steps — MVP-0 Step 10)

1. Provider preflight (P1–P6). P2/P3 fail ⇒ `STATUS: PROVIDER_INCOMPATIBLE`.
2. Health check.
3. GitHub webhook connectivity.
4. Read-only Issue fetch (no writes).
5. Coding agent creates the allowed file + Draft PR (sandbox).
6. Deterministic CI contract.
7. Mark PR ready for review.
8. Independent reviewer.
9. Reviewer comment requests feedback marker.
9b. **Round-2 label gate (orchestrator-owned):** the test orchestrator — and
    only the orchestrator — adds the `round-2` label to the original PR and
    reads the labels back to confirm presence. If the add FAILED, the read
    FAILED, or the label is ABSENT, **stop immediately** (status
    `ROUND_2_LABEL_GATE_FAILED`); do **not** fall back to round 1 and do **not**
    post the rework comment. Otherwise proceed.
10. Original agent amends same branch/PR (sandbox) to add
    `SECOND_ROUND_FEEDBACK_APPLIED`.
11. CI re-runs with `--round 2` (driven by the `round-2` label).
12. Reviewer re-reviews new head SHA.
13. Keep PR unmerged.
14. Generate final evidence report.

### GitHub App minimal permissions (round-2 label gate)

The orchestrator uses a GitHub App installation token scoped **only** to
`yzhlx/hermes-open-swe-smoke-test`:

- `pull_requests: read` — read PR labels (to confirm the gate).
- `pull_requests: write` — add the `round-2` label.

No other repository (not `yzhlx/hermes-open-swe-lab`, not
`yzhlx/hermes-learning-os`) and no other permission is required. The coding
agent and CI must never add the label.

## Retry budget (MVP-0 Step 15)

```text
provider_attempts = 2
agent_attempts    = 2
sandbox_attempts  = 2
review_rounds     = 2
ci_fix_rounds     = 1
```

No unlimited loops. After the budget, stop and preserve evidence.

## Stop conditions (emit status, halt)

- `USER_ACTION_REQUIRED` — need auth/web-auth/secret/LangSmith/payment.
- `PROVIDER_INCOMPATIBLE` — P2/P3 fail.
- `WEBHOOK_FAILED` — webhook not delivering.
- `AGENT_FAILED` — coding agent exhausted attempts.
- `CI_FAILED` — contract unfixable within `ci_fix_rounds`.
- `ROUND_2_LABEL_GATE_FAILED` — round-2 label add/read failed or label absent
  after the flow entered FEEDBACK_REQUESTED/REWORK_RUNNING; round 2 must stop,
  never degrade to round 1.
- `REVIEW_FAILED` — reviewer loop exhausted.
- `SANDBOX_ACCESS_REQUIRED` — LangSmith sandbox unavailable.
- `SERVER_RESOURCE_LIMIT` — Hermes health at risk.
- `SECURITY_BOUNDARY_VIOLATION` — any boundary breach.

## Logs & traces

- Control-plane logs: `/opt/hermes-open-swe-lab/logs/` (no secrets).
- LangSmith traces: project `hermes-open-swe-mvp0`.
- Evidence report: `OPEN-SWE-PHASE-1-RESULT.md` (updated each run).

## Shutdown

- Stop the control-plane process; close the ngrok tunnel.
- Leave the Draft PR open/unmerged; do not delete the branch.
- No production Hermes component is touched.
