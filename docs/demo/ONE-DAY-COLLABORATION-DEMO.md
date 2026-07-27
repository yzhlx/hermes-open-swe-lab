# ONE-DAY-COLLABORATION-DEMO-RUNNER

**Agent F — vertical slice.** A repeatable, single-task Demo orchestrator that
proves the full controlled-delivery lifecycle end-to-end against the *existing,
approved* canonical MVP-0 components.

- Branch / worktree: `demo/vertical-slice-runner-20260727`
- New files only (no core production code modified):
  - `tools/run_collaboration_demo.py`
  - `tests/test_collaboration_demo_runner.py`
  - `docs/demo/ONE-DAY-COLLABORATION-DEMO.md`
- Completion marker (this task is "ready" locally):
  `ONE_DAY_COLLABORATION_DEMO_RUNNER_READY_LOCAL`

---

## 1. What it does

Given one task it runs a deterministic pipeline:

| Step | Action | Notes |
|------|--------|-------|
| 1 | Verify the **real local Commit** exists | read-only `GitWorkspaceInspector` |
| 2 | Hand the Commit to the **Release Agent** | the only role that may deliver |
| 3 | Deliver **only through `DeliveryController`** | push + Draft PR, never merge |
| 4 | Obtain or reuse a **Draft PR** | idempotent; never a 2nd PR |
| 5 | Read **CI status** for that PR | via the GitHub client |
| 6 | CI not green → **STOP at `CI_PENDING`** | failure is never faked as success |
| 7 | CI green → produce **`FINAL_ACCEPTANCE`** | blocking on Human Owner |
| 8 | Wait for Human Owner `final_accept(token)` | token-checked |
| 9 | Call `complete()` | |
| 10 | Emit **`TASK_COMPLETED`** | |
| 11 | Emit a **structured status summary** | readable by "Buzz" |

---

## 2. Canonical components reused (never re-implemented)

| Component | Role in the demo |
|-----------|------------------|
| `hermes_worker.delivery.DeliveryController` | the ONLY controlled delivery path (push + Draft PR). Force-push rejected, draft forced, never merged, idempotent, fail-closed. |
| `hermes_worker.delivery.DeliveryAuthorization` | explicit task+repo+commit grant; never inferred from env/lease/agent text; never carries a token. |
| `hermes_worker.delivery.GitWorkspaceInspector` (Local / Fake) | read-only local-commit validation. |
| `hermes_worker.github_client.GitHubRestClient` / `FakeGitHubRestClient` | Draft PR creation + CI read. |
| `hermes_worker.repository.HostGitOperations` | the ONLY production git-push implementation. |
| `hermes_worker.constants` | single source of truth for the repo allowlist / protected set. |

The demo module **contains no delivery / git / GitHub logic of its own**. It
orchestrates. The `ReleaseAgent` class inside the tool is the demo-scoped
coordinator role — it is the *only* code path that calls
`DeliveryController.deliver()`.

---

## 3. Inputs

| Input | Meaning |
|-------|---------|
| `repository` | target GitHub repo |
| `task_id` | task / issue identifier (drives the delivery branch) |
| `worktree_path` | local repo path used for commit validation + push |
| `local_commit_sha` | the real local commit to deliver |
| `expected_sha` | task-record expected SHA (must equal `local_commit_sha`) |
| `title` / `body` | Draft PR metadata |
| `human_owner_token` | acceptance token checked in `final_accept` |

Construct via `DemoRunner(...)` directly, or the factories
`build_offline_runner(...)` / `build_live_smoke_runner(...)`.

---

## 4. Two run modes

### `offline`
All GitHub / git are Fake or Mock. Fully deterministic, no network, no token,
no real push/PR. Used by every test. CI status is supplied by an injected
`ci_provider` (a `Callable[[pr_number], str]`); the test harness drives
`pending` / `success` / `failure`.

### `live-smoke`
- **Only** `yzhlx/hermes-open-swe-smoke-test` is permitted. Any other repo —
  including the protected `yzhlx/hermes-learning-os` — raises
  `LIVE_SMOKE_REPO_NOT_ALLOWED` (or is blocked by the controller's protected-repo
  gate).
- Real Draft PRs go through `GitHubRestClient`; CI status through
  `RealGitHubClient` (`gh pr checks`).
- **Merge is never allowed.** No merge method is ever called on any path.

---

## 5. Hard prohibitions (enforced, fail-closed)

| Prohibition | Enforcement |
|-------------|-------------|
| No direct `gh push` | every push goes through `HostGitOperations` via `DeliveryController` |
| No direct `gh pr create` | every Draft PR goes through `GitHubRestClient` via `DeliveryController` |
| Never bypass the Release Agent | the demo may only deliver through `ReleaseAgent` → `DeliveryController.deliver` |
| Never access `yzhlx/hermes-learning-os` | protected-repo gate in `DeliveryController` + live-smoke repo check |
| Never auto-merge | no merge method exists/called anywhere on the path |
| Never fake failure as success | fail-closed state machine; a blocked step stays `BLOCKED`/`CI_PENDING`, never `COMPLETED` |

---

## 6. State machine

```
INIT → COMMIT_VERIFIED → DELIVERED → CI_PENDING ⇢ (CI not green, STOP)
                                  │
                                  └─→ FINAL_ACCEPTANCE_READY → ACCEPTED → COMPLETED
                                         ▲                         │
                                         │ final_accept(token)    │ complete()
                                         └─────────────────────────┘
BLOCKED  : any fail-closed delivery / commit / repo gate
REJECTED : a guarded transition refused (acceptance before CI, etc.)
```

Guards:
- `final_accept(token)` is **only** valid in `FINAL_ACCEPTANCE_READY`.
  Otherwise → `DemoRejected("ACCEPTANCE_BEFORE_CI")` (or `_REJECTED`).
- `complete()` is **only** valid in `ACCEPTED`.
  Otherwise → `DemoRejected("COMPLETION_BEFORE_ACCEPTANCE")`.
- The Human-Owner token is checked with constant-time comparison.

---

## 7. Buzz-readable structured summary

`DemoRunner.summary()` returns a JSON-serializable dict (schema
`hermes.demo.one_day_collaboration.v1`) with: `state`, `result`
(`TASK_COMPLETED` or `None`), `delivery_state`, `delivery_status_code`,
`pr_number`, `pr_url`, `ci_status`, `accepted`, `completed`, `rejection`,
`message`, SHAs, `task_id`, `repository`, `mode`, `ts`. `complete()` can also
write it to `summary_path` as JSON — that file is what an external reader agent
("Buzz") consumes.

---

## 8. Test coverage

`tests/test_collaboration_demo_runner.py` (all offline, deterministic):

| Requirement | Test |
|-------------|------|
| happy path | `test_happy_path_reaches_task_completed` |
| CI failure stops | `test_ci_failure_stops_at_ci_pending` / `test_ci_pending_stops_at_ci_pending` |
| duplicate run reuses PR | `test_duplicate_run_reuses_pr` |
| acceptance before CI rejected | `test_acceptance_before_ci_rejected` |
| completion before acceptance rejected | `test_completion_before_acceptance_rejected` |
| second run does not create second PR | `test_second_run_does_not_create_second_pr` |
| protected repo rejected | `test_protected_repo_rejected` |
| (guard) live-smoke wrong repo rejected | `test_live_smoke_rejects_wrong_repo` |
| (guard) wrong Human-Owner token | `test_wrong_human_owner_token_rejected` |
| (guard) no merge ever invoked | `assertEqual(...merge_calls, [])` in happy path + live wiring |
| (guard) live-smoke wiring | `test_live_smoke_wiring_reaches_acceptance_with_fakes` |

Run:

```bash
python -m unittest tests.test_collaboration_demo_runner -v
```

Offline self-demo (forces CI green, runs to `TASK_COMPLETED`):

```bash
python tools/run_collaboration_demo.py --mode offline
```

---

## 9. Status

`ONE_DAY_COLLABORATION_DEMO_RUNNER_READY_LOCAL` — the runner, its tests, and this
document are committed on `demo/vertical-slice-runner-20260727`; all required
scenarios pass offline. `live-smoke` is wired but requires the smoke-test
repository + GitHub App credentials to execute against real GitHub (not run in
this environment).
