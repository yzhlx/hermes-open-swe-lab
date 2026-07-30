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

---

## D2.5 — HermesDockerSandboxBackend real Docker daemon validation (2026-07-25)

**Scope:** validate the FULL container lifecycle of `HermesDockerSandboxBackend`
against the user's LOCAL Docker daemon. No GitHub, no real token, no webhook, no
relay model, no remote repos, no cloud Docker, no privileged, no Open SWE `local`
backend.

**Environment**
- Docker Desktop 4.63.0 (220185); Engine 29.2.1; context `desktop-linux`
  (Docker Desktop Linux VM, linux/amd64). `DockerRootDir` = `/var/lib/docker`.
- Test image: `hermes-d2-smoke:local` — `FROM python:3.11-slim` + `git` (+ca-certificates),
  built locally via the configured DaoCloud mirror. digest
  `sha256:f32d6f5fe9900b6d06f6eb46e0e8625f3a801a93c46f42d5093292225a0bdb3c`.
- Harness: `runtime/d2-real-smoke/run_real_smoke.py` (drives the REAL backend,
  `runner=None` → live `docker` CLI). Report: `runtime/d2-real-smoke/REPORT.json`.

**REAL `docker inspect` limits (proven, not asserted from command strings)**
| Limit | Value |
| --- | --- |
| CPUs | 1.0 (daemon stores as `NanoCpus=1000000000`) |
| Memory | 2147483648 (2 GB) |
| PidsLimit | 256 |
| Privileged | false |
| NetworkMode | bridge (NOT host) |
| AutoRemove | true |
| Mounts | exactly ONE bind: `<workdir> → /workspace` (rw) |

**Lifecycle results (real exit codes)**
| Step | Result |
| --- | --- |
| create | container `hermes-hermes-docker-…` (redacted prefix) |
| health_check | true |
| execute | exit 0 (bash 5.2.37, git 2.47.3 present) |
| write_file / read_file / edit_file | round-trip OK (edited content confirmed) |
| verify script | exit 0 (`bash repo/verify.sh`) |
| git_clone (local bare) | exit 0 |
| git_status / git_diff | exit 0 |
| git_commit | exit 0 (root-commit on `main`) |
| git_push → local bare remote | exit 0 (`* [new branch] main -> main`) |
| bare remote received commit | `c974e59 add README and verify script` (rc 0) |
| stop | container removed (auto_remove) |
| delete | `_container` → None; workdir wiped |

**Security / failure scenarios (real)**
- Container cannot see host docker.sock, SSH keys, or `.env`
  (`ls /var/run/docker.sock`, `/root/.ssh` → No such file).
- Failed exec records exit code (`sys.exit(7)` → 7).
- Timeout command killed (host-side subprocess timeout) → exit 124; container
  still cleaned on `delete()` (no residual).
- Path escape (`../../escape.txt`) **rejected** by backend `_safe_path()` guard
  (host FS escape prevention).
- `_calls` log contains no `GITHUB_TOKEN=` / `.pem` / `Authorization`.
- **Residual containers after run: 0.**
- Runtime: 12.08 s.

**Regression fix from this test**
- `HermesDockerSandboxBackend._safe_path()` added: rejects `..` / absolute paths
  that escape the workspace (write/read/edit). Regression test:
  `tests/test_d2_sandbox.py::test_11_path_escape_rejected`.

**Test result:** D1 7/7 + D2 offline 11/11 = **18/18 PASS**; D2 real Docker smoke
**PASS**. D2 completion CLAIMED for the real-lifecycle criterion.
