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
3. **Observe** via the SQLite Event Store (`runtime/events.db`) + the smoke-test
   repo PR/Checks. (LangSmith is RETIRED — ADR-002; observability is the Event Store.)
4. **Collect evidence**: PR number, check-run result, worker/event trace IDs,
   head SHAs, changed-file list.

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
- `SANDBOX_ACCESS_REQUIRED` — Docker sandbox unavailable (daemon down / container
  launch failed). (LangSmith sandbox is RETIRED — ADR-002.)
- `SERVER_RESOURCE_LIMIT` — Hermes health at risk.
- `SECURITY_BOUNDARY_VIOLATION` — any boundary breach.

## Logs & traces

- Control-plane logs: `/opt/hermes-open-swe-lab/logs/` (no secrets).
- Observability: SQLite Event Store (`runtime/events.db`) + `runtime/runs/*.jsonl`
  (no secrets; LangSmith RETIRED per ADR-002).
- Evidence report: `OPEN-SWE-PHASE-1-RESULT.md` (updated each run).

## Shutdown

- Stop the control-plane process; close the ngrok tunnel.
- Leave the Draft PR open/unmerged; do not delete the branch.
- No production Hermes component is touched.

---

## D2.5 — local Docker sandbox validation (no GitHub / no token)

Run this on the operator's LOCAL machine with Docker Desktop running. It
validates the real container lifecycle of `HermesDockerSandboxBackend` end to
end using only a local bare git remote — no GitHub, no real token, no webhook,
no relay model, no remote repo.

**Prereqs**
- Docker Desktop started (daemon reachable: `docker info` returns Server).
- Local image with `bash` + `git`: `docker build -t hermes-d2-smoke:local
  -f runtime/d2-smoke-Dockerfile runtime/` (FROM `python:3.11-slim` + `git`).
- Never bypass via cloud Docker, remote socket, privileged, or Open SWE `local`.

**Run**
```bash
python runtime/d2-real-smoke/run_real_smoke.py
# -> runtime/d2-real-smoke/REPORT.json  (pass: true expected)
```

**What it proves (real `docker inspect`, not just command strings)**
- Limits: cpus=1, mem=2GB, pids=256, privileged=false, net=bridge, auto-remove,
  single workdir bind mount.
- Lifecycle: create→health→execute→write/read/edit→git clone/status/diff/
  commit→push to local bare→stop→delete.
- Security: no docker.sock/SSH/.env visible; failed exec records exit code;
  timeout→124 + cleaned; `../` path escape rejected; no token/PEM/Auth in logs;
  0 residual containers.

**Troubleshoot**
- `USER_ACTION_REQUIRED` if `docker info` cannot reach the daemon → start Docker
  Desktop (or enable WSL Docker integration). Do NOT install Docker on the cloud
  server or expose the socket.
- A `../` path or absolute path to `write_file`/`read_file`/`edit_file` raises
  `PermissionError` (host FS escape guard) — this is expected and correct.
- Commit needs staging: the protocol `commit()` is a thin wrapper; the agent
  stages via `execute("git -C <repo> add -A")` first (mirrored in the harness).

**Cleanup**
- The harness deletes every container it creates and wipes the host workdir.
- Verify `docker ps -a --filter name=hermes-` is empty after the run.
