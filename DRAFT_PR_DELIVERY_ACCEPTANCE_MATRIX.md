# Draft PR Delivery Acceptance Matrix

> Independent test-design review of the **Host Worker GitHub Draft PR 受控交付层**
> (workspace `F:\project\hermes-open-swe-lab_codex`, branch `codex-primary-agent-policy`).
> This document is the acceptance matrix for downstream Reviewers. It contains **no test code** — only the matrix.

## 1. Repository State
- **Branch:** `codex-primary-agent-policy`
- **HEAD:** `3612b52` (feat: run codex executor through wsl2), 5 commits ahead of `origin/d3-integration`
- **Workspace clean:** yes (`nothing to commit, working tree clean`)
- **Scope note:** this is the `F:\project\hermes-open-swe-lab_codex` workspace. The protected repo `yzhlx/hermes-learning-os` was **not** accessed (no clone, no read, no grep against it).

## 2. Existing Test Infrastructure
**Reusable tests**
- `tests/test_codex_host_flow.py::CodexJobRunnerStateTests` — happy path + codex-fail/docker-fail/no-changes; `RepositoryPreparerTests` (askpass cleanup, token redaction); `DockerTestEvidenceTests`; `CodexCliRunnerTests` (credential-unset checks).
- `tests/test_d3_security.py`, `tests/test_d2_sandbox.py`, `tests/test_redact.py`, `tests/test_d3_closed_loop.py` — partially reusable for redaction + closed-loop assertions.
- `tests/deployment/conftest.py` — shared fixtures.

**Reusable fakes / fixtures**
- `test_codex_host_flow.py`: `FakeBroker`, `FakePreparer`, `FakeCodex`, `FakeDockerBackend`, `FakeGitOperations`, `FakeGitHub`, `FakeGitCommandRunner`, `FakeProcess`/`FakePopen`, `init_repo()`.
- `github_client.py`: `FakeGitHubClient` (records PRs, `merge_called` flag, `pr_numbers()`, `ci_map()`, `label_map()`), `GitHubRestClient` (real structure).
- `github_app.py`: `FakeAppApiClient` (records `exchanges` = installation_id + repos + ttl).
- `ControlPlane(..., allowed_token_hashes={hash_token(t)}, clock=...)` + `db.hash_token` for lease assertions; `redact()` for log/body assertions.

**Missing test helpers**
- `FakeGitHubRestClient` that captures the exact HTTP request (method/URL/headers-without-auth/parsed body) so PR `draft` flag, `base`/`head` order, body-token-absence, and `Authorization` presence can be asserted without network.
- Broker fake that can **raise** (`unknown_worker_token`, `job_not_owned_by_worker`, `job_not_active`, `repo_not_allowed`) and that **tracks token reuse** (asserts cache is zeroized after delivery).
- Git-command runner that can simulate dirty workspace / detached HEAD / amended commit / **non-fast-forward push**, and that **fails the test if it ever sees `--force`, `:refs/heads/…`, or a push-to-`base` refspec**.
- `run_local` fixture repo factory that can be set to dirty / detached / no-upstream / wrong-remote states.
- Idempotency oracle: a `FakeGitHub`/`FakeGitHubRestClient` pre-seeded with an existing PR for the branch, returning it instead of creating a new one.
- "Crash-before-PR" GitHub fake: records the push, then raises inside `create_draft_pr` (recovery test).
- Secret-scan stub + a "secret present in diff" fixture (to assert the currently-missing scan gate).

---

## 3. Authorization Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| AUTH-01 | Valid job, `worker_token` invalid/unknown | `run(job, …, worker_token="bogus")` | `cp._check_token` raises `unknown_worker_token` | none to broker/preparer/git/github | no `get_token_for_job`, no `prepare`, no `push`, no `create_draft_pr` | exception propagates OR `BLOCKED` (must finalize) | Unauthorized ⇒ no push, no PR | `test_codex_host_flow.py` |
| AUTH-02 | `repo` not in `ALLOWED_GITHUB_REPOS` (e.g. `yzhlx/other`) | `run(repo="yzhlx/other", …)` | `main()`/runner early `if repo not in ALLOWED…` | none | no claim, no prepare, no push, no PR | exception/`BLOCKED` (currently raises before claim → must verify terminal state written) | Unauthorized repo ⇒ blocked at gate | `test_codex_host_flow.py` |
| AUTH-03 | Env contains `GITHUB_TOKEN`/`HERMES_GIT_INSTALLATION_TOKEN` | env token present, broker issues its own lease-scoped token | `HostGitOperations.push` receives broker token; git-runner asserts token == broker token ≠ env token | `push` with broker token | push never called with env token; no `gh auth` | `PR_CREATED` (or `BLOCKED` if no broker) | Env token ≠ user authorization | `tests/test_codex_host_flow.py` |
| AUTH-04 | Worker owns job A only | `get_token_for_job(cp, job_B, token_A_worker)` | broker re-checks `worker_token_hash==h` and `state in active` | none | no token returned for job B | raises `job_not_owned_by_worker` | One task's auth not reusable for another task | `tests/test_github_app_broker.py` |
| AUTH-05 | Job repo = protected/other repo | `get_token_for_job` for job whose repo ∉ allowed | broker re-derives repo from job, checks `allowed_repos`; `FakeAppApiClient.exchanges` shows `repositories=[repo]` | none | no token minted for other repo | raises `repo_not_allowed` | One repo's auth not reusable for another repo | `tests/test_github_app_broker.py` |
| AUTH-06 | Authorization object carries a `commit_sha` binding | run with auth whose bound SHA ≠ actual commit | (none — **not implemented**) | n/a | n/a | **currently NOT ENFORCED** → must be added | Commit SHA in auth must match | `tests/test_codex_job_runner_auth.py` (BLOCKER gap) |

## 4. Repository and Git Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| REPO-01 | Protected repo `yzhlx/hermes-learning-os` | `run(repo=protected)` | `ALLOWED_GITHUB_REPOS` excludes it; `PROTECTED_REPOS` defined but **never referenced** | none | no prepare/push/PR | blocked (allowlist) | Protected repo always rejected | `test_codex_host_flow.py` + `test_constants.py` |
| REPO-02 | Non-allowlist repo | `run(repo="some/rand")` | allowlist exact-match fails | none | no network/git | blocked | Non-allowlist rejected | `test_codex_host_flow.py` |
| REPO-03 | `repo` variants: `"Yzhlx/Hermes-Open-SWE-Smoke-Test"`, `"…/"`, `"git@github.com:…git"`, `"https://github.com/…git"`, `"ssh://git@…"` | each variant as `repo` | no canonicalization today → all rejected by exact match | none for bypass | no push/PR for any variant | **fails closed** but legit non-canonical also rejected → recommend canonicalize-then-check | Case/URL/scheme must not bypass allowlist | `tests/test_repo_canonicalize.py` |
| REPO-04 | `prepare` called | `prepare(dest, repo, base, branch, token)` with SSH/HTTPS URL forms | `RepositoryPreparer` builds `https://github.com/{repo}.git` only after allowlist pass | `init`, `remote add origin https://github.com/{canon}.git`, `fetch`, `checkout -b` | no `clone`; no credential in URL; no `GIT_CONFIG_*` skip | `ok=True` only for canonical allowed | SSH/HTTPS normalized before decision | `test_codex_host_flow.py::RepositoryPreparerTests` |
| REPO-05 | Push to default branch attempted | `HostGitOperations.push(repo, branch="main", token)` | git-runner **intercepts** any refspec targeting `base`/`main` | `push HEAD:refs/heads/<topic>` | never `refs/heads/main`; never `+`/force | push refused by test harness | Default branch never directly pushed | `tests/test_host_git_ops.py` |
| REPO-06 | Force-push attempted | `push` with `--force` injected | git-runner asserts args contain no `--force` | normal push | `--force` / `+refs/…` | rejected | No force push | `tests/test_host_git_ops.py` |
| REPO-07 | Remote-branch delete attempted | `push ":refs/heads/x"` | git-runner asserts no `:` delete refspec | none | `:refs/heads/…`, `--delete` | rejected | No remote branch deletion | `tests/test_host_git_ops.py` |
| REPO-08 | `run_local` repo is dirty / has untracked files | `run_local` with modified+untracked files | `FakeGitOperations.changed_files` returns them | `commit` (git add --all) | (assert no secret files staged) | `completed` (local) — define expected: MUST NOT push; recommend secret-scan gate | Dirty workspace handled without leak | `test_codex_host_flow.py` |
| REPO-09 | `run_local` repo detached HEAD / no upstream / wrong remote | `run_local` on such repo | fixture repo factory sets state | `commit` only | no `push`, no remote mutation | `completed` (local) — assert no push regardless of remote | Local Git anomalies don't escalate to push | `tests/test_host_git_ops.py` |

## 5. Push and Draft PR Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| PUSH-01 | Push fails (exit≠0) | `FakeGitOperations.push`→exit 1 | push fails before PR | `prepare`,`commit`,`push` | `create_draft_pr` | `BLOCKED` | Push fail ⇒ no PR | `test_codex_host_flow.py` |
| PUSH-02 | Happy path | all green | `push`→0 then `create_draft_pr` | `push` THEN `create_draft_pr` (ordered) | PR before push | `PR_CREATED` | Push must precede PR | `test_codex_host_flow.py` |
| PUSH-03 | PR must be Draft | `create_draft_pr` returns `draft=True` | `FakeGitHubRestClient` captures body+`draft` | POST `/repos/…/pulls` with `"draft":true` | `draft:false` accepted | `PR_CREATED` | PR forced to Draft | `tests/test_github_rest_client.py` |
| PUSH-04 | GitHub ignores draft → non-draft returned | `create_draft_pr` returns `draft=False` | runner checks `pr.get("draft")` | push only | no `PR_CREATED` | `BLOCKED` | Refuse non-draft PR | `test_codex_host_flow.py` |
| PUSH-05 | base/head order | `create_draft_pr(repo, task_branch, base, …)` | assert `head=task_branch`, `base=base` | correct order | reversed (head==base) | `PR_CREATED` | head≠base, head≠default | `tests/test_github_rest_client.py` |
| PUSH-06 | PR body must not contain credential | body = static string | `FakeGitHubRestClient` records body; assert `FAKE_TOKEN`/`ghs_`/`Bearer` absent | PR with static body | token in body | `PR_CREATED` | No secret in PR body | `tests/test_github_rest_client.py` |
| PUSH-07 | No auto-merge | full success | `FakeGitHubClient.merge_called` flag | none | `merge_pr` | `PR_CREATED` (never merged) | Automation never merges | `test_codex_host_flow.py` |
| PUSH-08 | PR creation raises | `create_draft_pr` raises | outer `except` → `_finish BLOCKED` | push already done | no `PR_CREATED` | `BLOCKED` | PR-fail ⇒ not completed | `test_codex_host_flow.py` |

## 6. Idempotency and Recovery Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| IDEM-01 | Same task+commit+repo+branch re-run | `run` twice | second run prepares fresh worktree, pushes same `task_branch` | push (likely non-ff→fail) + (today) a 2nd PR | **should not create 2nd PR** | today: 2nd `PR_CREATED` with new PR# (GAP) | No duplicate PR on re-run | `test_codex_host_flow.py` (BLOCKER) |
| IDEM-02 | Existing identical Draft PR | PR already exists for branch | idempotency oracle returns existing PR | `get_job_by_pr`/lookup, **reuse** | new `create_draft_pr` | `PR_CREATED` reusing PR# | Duplicate PR prevented | `tests/test_codex_job_runner_idem.py` (GAP) |
| IDEM-03 | PR already closed | existing PR `state=closed` | oracle returns closed PR | lookup + escalate, no new PR | reopen/new PR | `BLOCKED` or reuse-as-documented | Closed PR handled | `tests/…idem.py` (GAP) |
| IDEM-04 | PR already merged | existing PR `merged=True` | oracle returns merged PR | none | new PR / re-push merge | `BLOCKED` | Merged not re-delivered | `tests/…idem.py` (GAP) |
| IDEM-05 | Same task, changed commit | re-run with new commit | new commit on same branch | push new commit (or reject) | silent overwrite w/o record | defined (PR_CREATED or BLOCKED) | Commit change tracked | `tests/…idem.py` (GAP) |
| IDEM-06 | Same commit, different task_id | two jobs, same SHA | separate job ids | separate PRs (acceptable) OR blocked | cross-task reuse without check | documented | Different task = separate delivery | `tests/…idem.py` |
| IDEM-07 | Push OK, crash before PR | push→0, `create_draft_pr` raises | crash simulator | push recorded; PR not created | orphan PR | recovery must resume to PR (not 2nd push) | Crash-safe delivery | `tests/…idem.py` (GAP) |
| IDEM-08 | PR created, state not written back | `create_draft_pr`→ok, then `_finish` fails | simulate write failure | push+PR done | `PR_CREATED` lost silently | terminal state must persist atomically | Status durability | `tests/…idem.py` (GAP) |
| IDEM-09 | Network retry on PR | `create_draft_pr` transient error then success | retry wrapper + oracle dedup | exactly one PR | duplicate PR on retry | `PR_CREATED` single PR# | Retries don't duplicate | `tests/…idem.py` (GAP) |

> The `CodexJobRunner.run` path currently consults **none** of `ControlPlane.get_job_by_pr` / `create_issue_task` / `record_delivery` (those live in the separate `scheduler.py` D3 path). Idempotency for the new host delivery layer is effectively unimplemented — this is the central finding.

## 7. Credential Isolation Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| CRED-01 | Full run | `FAKE_TOKEN` flows broker→askpass→git | `redact`-style assert on all events/errors | all steps | `FAKE_TOKEN` in any event/error/stdout | `PR_CREATED` | Token not in logs | `test_codex_host_flow.py` |
| CRED-02 | Exception path | codex raises with token in msg | `_finish` redacts error | none | token in stored error | `BLOCKED` | Token not in exception/error | `tests/test_redact.py` |
| CRED-03 | PR body | static body | `FakeGitHubRestClient` body capture | PR | token in body | `PR_CREATED` | Token not in PR body | `tests/test_github_rest_client.py` |
| CRED-04 | Coding agent | codex invoked with `task` | `CodexCliRunner` env stripped of `GITHUB_TOKEN`/app keys (existing test) | codex.run(task) | token in codex env/args | pass | Token not to Coding Agent | `test_codex_host_flow.py::CodexCliRunnerTests` |
| CRED-05 | Docker | docker runs after push | `FakeDockerBackend` gets repo_path only | `run_tests` | token in docker args/env | pass | Token not into Docker | `test_codex_host_flow.py` |
| CRED-06 | Fixtures | offline tests | `FakeGitHubClient`/`FakeAppApiClient` only | none | real token written to fixture | n/a | Token not in fixtures | (process) |
| CRED-07 | Subprocess args | `HostGitOperations.push` | git-runner captures args; assert `HERMES_GIT_INSTALLATION_TOKEN` not in `args` (only in `env`) | `git push … HEAD:refs/heads/x` | token in args list | pass | Subprocess args clean | `tests/test_host_git_ops.py` |
| CRED-08 | Remote URL masking | `remote add origin https://github.com/{repo}.git` | capture remote URL in events | add remote | credential in URL (`https://TOKEN@…`) | pass | Remote URL credential-masked | `test_codex_host_flow.py::RepositoryPreparerTests` |

## 8. State Machine Tests

| ID | Preconditions | Input | Mock/Fake behavior | Expected calls | Forbidden calls | Expected state | Main security purpose | Suggested file |
|---|---|---|---|---|---|---|---|---|
| STATE-01 | authorization missing | `run` with bad token | raises before/at claim | none | `push`/`PR` | `BLOCKED` (must finalize) | No `completed` on auth fail | `test_codex_host_flow.py` |
| STATE-02 | repository blocked | `repo` mismatch / not allowed | `_finish BLOCKED` before try | none | push/PR | `BLOCKED` | No `completed` on repo block | `test_codex_host_flow.py` |
| STATE-03 | dirty workspace | `run_local` dirty (define policy) | fixture dirty | commit only | push | `completed`(local) / define host policy | No `completed` mislabel | `tests/test_host_git_ops.py` |
| STATE-04 | commit mismatch | recorded SHA ≠ actual (GAP: no check) | (none) | n/a | n/a | **not enforced** | Commit SHA verified | `tests/test_codex_job_runner_auth.py` (GAP) |
| STATE-05 | secret scan failed | diff contains secret (GAP: no scan) | (none) | n/a | n/a | **not enforced** | Secret gate exists | `tests/test_secret_scan.py` (GAP) |
| STATE-06 | push failed | push→exit 1 | →`_finish BLOCKED` | push | PR | `BLOCKED` | No `completed` on push fail | `test_codex_host_flow.py` |
| STATE-07 | PR failed | `create_draft_pr` raises/non-draft | →`BLOCKED` | push | `PR_CREATED` | `BLOCKED` | No `completed` on PR fail | `test_codex_host_flow.py` |
| STATE-08 | idempotency conflict | duplicate PR attempt (GAP) | (none) | n/a | n/a | **not enforced** | No `completed` on conflict | `tests/…idem.py` (GAP) |
| STATE-09 | unexpected exception | mid-run raise | outer `except`→`_finish BLOCKED`; assert `finish_state(…,"completed")` raises `invalid_terminal_state` | `_finish BLOCKED` | `completed` from `run` | `BLOCKED`; `finish_state` rejects `completed` | All failures stay out of `completed` | `tests/test_control_plane.py` + `test_codex_host_flow.py` |

## 9. Highest-Risk Scenarios
- **BLOCKER — Idempotency not implemented in `CodexJobRunner.run`** (IDEM-01/02/03/04/07/08/09): re-runs mint duplicate Draft PRs and duplicate pushes; no `get_job_by_pr` reuse, no crash-resume, no retry-dedup. A single task retried N times produces N PRs.
- **BLOCKER — Commit-SHA binding in authorization absent** (AUTH-06 / STATE-04): "授权对象中的 Commit SHA 不匹配时必须拒绝" has no code path enforcing it.
- **BLOCKER — No pre-push secret scan** (STATE-05): there is no `secrets_in_diff` gate in the host delivery layer (it exists only in the unrelated `scheduler.py`), so "secret scan failed" is not a reachable failure path.
- **HIGH — `PROTECTED_REPOS` defined but never referenced** (REPO-01): protection is implicit via allowlist only; an accidental widen of `ALLOWED_GITHUB_REPOS` would silently expose the production repo. Add an explicit deny check.
- **HIGH — No repo-name/URL canonicalization** (REPO-03): allowlist is exact-match; case/SSH/HTTPS/trailing-slash variants are rejected (fails closed) but a legitimate non-canonical form is *also* rejected, tempting operators to add variants to the allowlist. Add a canonicalizer (lowercase, strip scheme/`+`/`.git`/trailing slash, split on `:`/`/`) before the exact check.
- **HIGH — Authorization-missing raises instead of finalizing state** (AUTH-01/02 / STATE-01): the early `raise ControlPlaneError("repo_not_allowed")` (and `claim_job` auth failure) occur *before* the `try`, so the job may remain in a non-terminal `running`/`claimed` state with no `BLOCKED` record. Wrap so every rejection writes a terminal state.
- **MEDIUM — No explicit `task_branch != base/default` guard at push layer** (REPO-05): structurally safe via the `codex/job-…` naming, but unasserted; add an explicit check + a test that fails the run if refspec targets `base`.
- **MEDIUM — No remote-URL verification before push in `run_local`** (REPO-09): `run_local` never pushes, but if it ever gains push, verify remote == allowed repo first.
- **MEDIUM — Pushed SHA not verified against recorded/expected SHA** (STATE-04-adjacent): no check that the pushed commit is the one recorded, nor that it is on the expected branch (amend/replace/detached not detected in the host path).

## 10. Minimum Acceptance Gate
The implementation may enter independent review only after **all** of the following pass:
1. AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05 (authorization gates block push/PR; env token ≠ auth; per-task/per-repo isolation).
2. REPO-01, REPO-02, REPO-05, REPO-06, REPO-07 (protected/non-allowlist rejected; no default-branch push; no force push; no branch delete).
3. PUSH-01, PUSH-02, PUSH-03, PUSH-04, PUSH-05, PUSH-07, PUSH-08 (push-before-PR ordering; Draft enforced; base/head correct; no auto-merge; PR-fail ⇒ not completed).
4. CRED-01…CRED-08 (token absent from logs/errors/body/agent/docker/args/remote-URL).
5. STATE-01, STATE-02, STATE-06, STATE-07, STATE-09 (`finish_state` rejects `completed`; every failure path lands in a terminal non-`completed` state).
6. **Plus a documented decision** for each BLOCKER gap above (AUTH-06, STATE-04, STATE-05, IDEM-*) — either implemented-and-tested, or explicitly accepted-as-out-of-scope with a tracking issue. Re-review must not pass with these silently unhandled.

## 11. Safety Confirmation
- Files modified: **none** (read-only review)
- Commit created: **none**
- Push performed: **none**
- PR modified: **none**
- WSL Smoke executed: **none**
- Protected repository (`yzhlx/hermes-learning-os`) accessed: **none**

---

**Bottom line for the reviewer:** the *structural* boundaries (allowlist exact-match, askpass token isolation, push-before-PR, Draft enforcement, redaction, no auto-merge, `finish_state` rejecting `completed`) are solid and testable today. The **controlled-delivery intent is not yet closed** on three axes that the test plan was specifically built to catch: (1) **idempotency** in `CodexJobRunner.run` is essentially absent, (2) **commit-SHA binding** in the authorization object is not implemented, and (3) there is **no pre-push secret scan** in the host path. Those three are the highest-priority items for the main agent before this layer can be called "controlled."
