# D4 Canonical Components Integration Report

## 1. Starting State
- Workspace: `F:\project\hermes-open-swe-lab`
- Branch: `d4-delivery-layer`
- Starting HEAD: `710d88e35a4402933426c7254d89117146da10d9` (confirmed MATCH)
- Workspace clean: yes (tracked files clean; only untracked items present)
- Untracked reports: `D4-DELIVERY-REPORT.md`, `D4-RECONCILIATION-REPORT.md`, `DRAFT-PR-DELIVERY-RECON.md`, `DRAFT_PR_DELIVERY_ACCEPTANCE_MATRIX.md` (all excluded from the commit)
- Frozen branch touched: NO (`3612b526...` unchanged; work done only on `d4-delivery-layer`)

## 2. Files Ported
| File | Source commit | Source blob | WSL dependency included |
|---|---|---|---|
| `hermes_worker/constants.py` | `8248268` | `ff9b9bdf20eb645bead76de6937a02aa0c5054c4` | none |
| `hermes_worker/repository.py` | `8248268` | `41bddd052ff0933cdef2f4a6edaaaedac78dfcbf` | none |
| `hermes_worker/github_app.py` | `8248268` | `f89d08c622b36cb448a7b089f8fb0c95b3e13921` | none |
| `hermes_worker/github_client.py` | `8248268` | `545f4498e3d057ccadd640f1831021743462fce5` | none |

All four blobs are byte-identical (`git hash-object`) to frozen `3612b526`. Extracted read-only via `git show`; no cherry-pick; no WSL/Codex files touched.

## 3. Canonical Reuse
- ALLOWED_GITHUB_REPOS: reused from `constants.py` (identity check passes; no D4 copy)
- PROTECTED_REPOS: reused from `constants.py` (identity check passes; no D4 copy)
- RepositoryPreparer: imported from `repository.py` (host-side repo prep; consumed by Host Worker, not re-implemented in delivery)
- HostGitOperations: imported from `repository.py` — the ONLY production git-push implementation (AskPass + single env allowlist + single ref regex)
- GitHubAppTokenBroker: canonical in `github_app.py`; wired as the production `token_provider` source (NOT imported into delivery.py to keep it decoupled; exercised in tests via `broker.mint_installation_token`)
- GitHubRestClient: canonical in `github_client.py` — production Draft-PR creation
- FakeGitHubClient: canonical in `github_client.py` — offline PR stand-in
- db.py: reused unchanged (`init_db`, `delivery_state`, `upsert_delivery_state`, `select_delivery_state`, `select_delivery_state_by_task`); single SQLite store, no second connection/registry
- redact.py: reused unchanged — single redactor for body/error/secret-scan
- ControlPlaneError: `DeliveryError` subclasses `control_plane.ControlPlaneError` (no second error hierarchy)

## 4. Parallel Implementations Removed
- D4 GitHubClient: REMOVED (the ABC that did push + PR API). `hasattr(delivery, 'GitHubClient')` → False
- D4 push implementation: REMOVED (`GhCliGitHubClient`). Push now exclusively via `HostGitOperations.push(repo_path, branch, token)`
- D4 allowlist: REMOVED (now imports `ALLOWED_GITHUB_REPOS`)
- D4 protected repo list: REMOVED (now imports `PROTECTED_REPOS`)
- D4 credential path: REMOVED (token only via injected `token_provider` → `HostGitOperations`/`GitHubRestClient`)
- D4 Git implementation: REMOVED as a second implementation; `GitWorkspace` → `GitWorkspaceInspector` (read-only only; no push/commit/cred logic); reuses `repository._SAFE_REF_RE` for branch char-class
- Remaining adapters and justification:
  - `GitWorkspaceInspector` (read-only local-commit validation: HEAD/detached/dirty/commit-in-history/empty/credential-in-diff/diff) — retained because `HostGitOperations` does not provide these read-only checks; contains no push/commit/credential/remote-write logic.
  - `FakeHostGitOperations` (offline) — records push calls, returns `CommandResult`, no git executed; a test seam, not a production implementation.
  - `FakeGitHubRestClient(FakeGitHubClient)` (offline) — adds the production-shaped `create_draft_pr(repo, branch, base, title, body, token)` signature plus `get_pr_by_head`; param/result conversion only, no HTTP/token/draft logic re-implemented.

## 5. Delivery Flow
```
deliver(...)
  1. Authorization gate (task_id + repository + commit_sha binding, fail-closed)
  2. Protected repo check (canonical PROTECTED_REPOS, before allowlist) → else allowlist check
  3. Force push / remote-branch deletion rejected
  4. Branch policy (reuses repository._SAFE_REF_RE; rejects default/protected + path injection)
  5. Idempotency key = repo|task|commit|branch
  6. Local-commit validation via GitWorkspaceInspector (read-only)
  7. Pre-push diff secret scan (reuses redact)
  8. Idempotency / conflict / recovery resolution (registry)
  9. Build PushPlan + DraftPrRequest (draft forced)
 10. Dry-run short-circuit (no writes)
 11. token = token_provider(repository)   # local var only, never stored
     HostGitOperations.push(repo_path=git.path, branch=remote_branch, token=token)
        → on failure/non-zero exit: PUSH_FAILED, no PR
 12. GitHubRestClient/FakeGitHubRestClient:
        get_pr_by_head(repository, remote_branch)  # reuse / closed / merged
        create_draft_pr(repo, branch=remote_branch, base, title, body, token)
        → non-draft returned → PR_CREATION_FAILED (fail-closed)
        → success → COMPLETED (never merge)
```

## 6. Security Gates
- Explicit authorization: `DeliveryAuthorization` (task+repo+commit) required; env/lease/agent text do NOT count
- Task/repo/commit binding: enforced in gate + final SHA match
- Protected repo: canonical `PROTECTED_REPOS` checked before allowlist; unconditional block even if mis-added to allowlist
- Allowlist: canonical `ALLOWED_GITHUB_REPOS`
- Dirty/detached/commit validation: read-only inspector gates (dirty, detached HEAD, commit-not-in-history, empty commit, SHA mismatch)
- Secret scan: pre-push diff scan via redact; hit → block before any push/PR
- Default branch: rejected by branch policy before push
- Force/delete protection: force rejected at gate; delete refspec rejected at branch policy; `HostGitOperations.push` has no force param and always uses `HEAD:refs/heads/{branch}`
- Credential isolation: token is a `deliver()` local variable; never on controller/DB/PR body/logs; passed only as a parameter to `HostGitOperations` (AskPass env) and `GitHubRestClient` (Bearer header)
- Draft-only: `DraftPrRequest.draft` forced True; non-draft GitHub response → PR_CREATION_FAILED
- No merge: no merge method is ever invoked (formal clients refuse; `merge_calls` stays empty)
- Idempotency: registry key + state machine; same delivery → no second PR
- Crash recovery: PUSHED/PR_FAILED state → resume PR only (no repeat push); existing open PR reused; closed/merged → fail-closed

## 7. Tests
- Commands:
  - `python -m pytest tests/test_d4_delivery.py -q`
  - `python -m pytest tests/ -q` (full suite)
- D4 passed: 54 / 54 (all offline integration tests)
- Full suite passed: 107 passed
- Failed: 2 (`tests/test_redact.py::test_redact_github_pat_in_command`, `tests/test_redact_secret_env_value`) — PRE-EXISTING on baseline HEAD; `redact.py` is unchanged by this task; unrelated to the integration
- Skipped: 0
- Canonical integration tests: importability of `constants`/`repository`/`github_app`/`github_client`; no `codex_cli_runner`/`codex_job_runner`; D4 parallel `GitHubClient` removed; `FakeGitHubRestClient` is canonical subclass; controller calls `HostGitOperations.push`; push params contain no token leak / no force / no delete refspec / no default branch; token only via formal provider; broker-backed `token_provider`; PR `draft=true`, base/head correct, body redacted, non-draft blocked, no merge; authorization (missing/task/repo/commit mismatch; token/lease not a substitute); idempotency/recovery; secret isolation
- Missing tests: none required by the task spec

## 8. Safety Confirmation
- Cherry-pick: NOT performed
- Merge: NOT performed
- Rebase: NOT performed
- Push: NOT performed (offline fakes only)
- Real PR: NOT performed
- Real credentials: NOT used (offline fakes; broker exercised with `FakeAppApiClient`)
- Protected repository accessed: NO (`yzhlx/hermes-learning-os` never touched)
- WSL Smoke: NOT run
- WSL/Codex files introduced: NONE (grep for `wsl`/`codex_cli_runner`/`codex_job_runner` in ported files → none)
- Frozen branch modified: NO (`3612b526...` untouched)

## 9. Local Commit
- Created: yes
- SHA: `8a5e695f5ed62744c9f40e261b06f2c9c347e7c4`
- Parent: `710d88e35a4402933426c7254d89117146da10d9` (history `a597531` preserved; no amend/reset/rebase/squash)
- Title: `D4: integrate canonical host git and GitHub delivery components`
- Files: `hermes_worker/constants.py` (new), `hermes_worker/repository.py` (new), `hermes_worker/github_app.py` (new), `hermes_worker/github_client.py` (new), `hermes_worker/delivery.py` (modified), `tests/test_d4_delivery.py` (modified). The 4 untracked report Markdown files were deliberately excluded.

## 10. Remaining Limitations
- The 2 `test_redact.py` failures are pre-existing baseline issues (redact.py unchanged by this task); they are NOT evidence of D4 integration regression and were not "passed off" as real GitHub end-to-end success.
- `FakeGitHubRestClient.get_pr_by_head` is an offline seam; the production `GitHubRestClient` does not yet implement a head-branch query — in production the Host Worker must supply a client that does (or rely on the registry-only closed/merged detection). The D4 offline tests cover the query path via the canonical-subclass seam.
- No real GitHub end-to-end was executed; all PR/push assertions are against offline fakes. The report does NOT claim real GitHub E2E PASS.

## 11. Final Status
INTEGRATION_PASS
