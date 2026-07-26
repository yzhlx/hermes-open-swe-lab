# D4 Controlled Delivery Reconciliation Report

## 1. Starting State
- **Workspace:** `/f/project/hermes-open-swe-lab`
- **Branch:** `d4-delivery-layer`
- **Starting HEAD:** `a597531b083d8815046464e44c9c48308ff24f1f` (the preserved D4 commit from the prior phase)
- **Workspace clean:** No tracked changes pending before this task. Only untracked prior report docs exist (`D4-DELIVERY-REPORT.md`, `DRAFT-PR-DELIVERY-RECON.md`, `DRAFT_PR_DELIVERY_ACCEPTANCE_MATRIX.md`) — no source modifications.
- **a597531 preserved:** Yes — it is the parent of the new follow-up commit; untouched, not amended/reset/rebased.
- **WSL commit/worktree touched:** No.

## 2. Reconciliation
**Duplicate components removed:**
- Removed the self-contained `SqliteDeliveryRegistry` (its own `sqlite3` connection **and** its own `delivery_registry` table). Replaced by `DbBackedDeliveryRegistry`, which uses `db.init_db` against the **single shared DB file** with a new `delivery_state` table whose schema is owned by `db.py`. There is now exactly one SQLite store / one schema owner for the worker.
- Removed the duplicate redaction path: `delivery.py` already imported `redact.redact`; confirmed there is no second redactor in this worktree.
- Removed the separate error hierarchy: `DeliveryError` now **subclasses** `control_plane.ControlPlaneError`, so the delivery layer shares the control plane's single error taxonomy.
- No second allowlist/protected constant existed in this worktree (canonical `constants.py` is on the frozen codex branch), so there was nothing to dedupe here.

**Existing components reused:**
- `hermes_worker/db.py` — genuine reuse: `init_db` + a new `delivery_state` table + `upsert_delivery_state` / `select_delivery_state` / `select_delivery_state_by_task` accessors. Idempotency/recovery persist through this single store.
- `hermes_worker/redact.py` — genuine reuse: `redact()` is the one redactor for PR bodies, error text, and the diff secret scan.
- `hermes_worker/control_plane.py` — genuine reuse: `ControlPlaneError` is the base of `DeliveryError`; fail-closed terminal-state discipline preserved.

**Components still retained and why:**
- `GitWorkspace` / `LocalGitWorkspace` / `FakeGitWorkspace` — D4-specific local-commit / working-tree validation seam. The canonical `repository.HostGitOperations` exists **only** on the frozen `codex-primary-agent-policy` branch. Copying it in would create the *second parallel implementation* this task forbids and would risk WSL Codex coupling. It is therefore retained as the only git-validation implementation in **this** worktree, documented as the single source here.
- `GitHubClient` / `FakeGitHubClient` / `GhCliGitHubClient` — D4-specific GitHub write seam (`push` / `get_pull_request` / `create_draft_pr`). The canonical `github_client.GitHubRestClient` exists **only** on the frozen codex branch; same rationale for retention.
- `PROTECTED_REPOS` / allowlist — single source of truth within this worktree (there is no `constants.py` here); the canonical `ALLOWED_GITHUB_REPOS` / `PROTECTED_REPOS` live on the frozen codex branch.
- `DeliveryState` / `PushPlan` / `DraftPrRequest` / `InMemoryDeliveryRegistry` — D4-specific data/coordination objects and the in-memory registry (not a "second SQLite"). Retained per the task's allowance for D4-specific data objects, blockers, adapters, and the coordination function.

## 3. Required Gaps
- **Pre-PR authorization:** Added and fail-closed. `DeliveryAuthorization` now binds `task_id + repository + commit_sha`; `deliver()` verifies `authorization.commit_sha == local_commit_sha` before any push. Closes AUTH-06 / STATE-04.
- **Task/repo/commit binding:** Enforced (above) — a mismatched commit, task, or repo blocks before push; a present env token / worker lease / agent claim does **not** substitute.
- **Protected repository guard:** `PROTECTED_REPOS` is checked *before* the allowlist and always blocks `yzhlx/hermes-learning-os`, even if it were added to the allowlist (tested).
- **Diff secret scan:** New `hermes_worker/secret_scan.py` reuses `redact`; wired pre-push; returns only redacted hit summaries; blocks delivery on any hit; the raw secret never enters the message, body, or log. Closes STATE-05 / recon gap.
- **Independent delivery entry:** `DeliveryController.deliver(repo, task_id, commit_sha, workspace, base_branch, remote_branch, authorization, …)` is the Codex-decoupled entry; WSL Codex is untouched.
- **Idempotency:** Key = `repository|task_id|commit_sha|remote_branch`; persisted in `db.py`; `get_pull_request` reuse prevents a second PR; `PUSHED`/`PR_FAILED`/`PR_CLOSED`/`PR_MERGED` resume correctly; same-key duplicate suppressed; same-task commit change detected as conflict.
- **Crash/retry recovery:** Push-then-crash resumes PR creation **without re-push**; a PR-created-state writeback failure recovers via `get_pull_request` reuse (no duplicate PR). Covers IDEM-07/08/09.

## 4. Files Changed
- `hermes_worker/delivery.py` — rewritten as a thin coordinator: removed `SqliteDeliveryRegistry` (→ `DbBackedDeliveryRegistry` on `db.py`); `DeliveryError(ControlPlaneError)`; authorization now binds `commit_sha`; added `is_detached_head` + `get_commit_diff` + `get_pull_request`; secret-scan hook; recovery/resume logic; `PR_CLOSED`/`PR_MERGED` fail-closed; non-draft returned → refused.
- `hermes_worker/secret_scan.py` — **NEW** small module, reuses `redact`.
- `hermes_worker/db.py` — added `delivery_state` table to `SCHEMA` + `upsert_delivery_state` / `select_delivery_state` / `select_delivery_state_by_task` (single DB ownership).
- `tests/test_d4_delivery.py` — expanded to **41 tests** covering the acceptance matrix.
- `D4-RECONCILIATION-REPORT.md` — this report.

## 5. Tests
- **Commands:** `"<managed-python>" -m unittest tests.test_d4_delivery` and full `discover -s tests -p "test_*.py"`.
- **D4 passed:** 41 / 41.
- **Full suite passed:** 74 / 75. The single error is the **pre-existing** `tests/test_adapter.py` (`import pytest` missing in the managed venv) — unrelated to this task and present before reconciliation (same as the prior phase).
- **Failed (D4):** 0. **Skipped:** 0.
- **Acceptance matrix cases covered:**
  - *Authorization:* missing blocks push/PR; task mismatch; repository mismatch; **commit-SHA binding mismatch**; wrong grant; env token ≠ authorization.
  - *Repository/Git:* protected repo rejected (even if added to allowlist); non-allowlist rejected; dirty workspace; **detached HEAD**; commit not in history; empty commit; task-record SHA mismatch; **default-branch push rejected**; **force push rejected**; **branch-delete refspec rejected**; push target matches allowlist.
  - *Secret scan:* diff with simulated credential blocks; raw secret value absent from message/error/body; no-hit continues.
  - *Push/Draft PR:* push-fail → no PR; **push before PR (ordered)**; `draft=true` forced; base/head order correct; **non-draft returned → refused**; PR-fail → not completed; **no merge ever called**.
  - *Idempotency/Recovery:* duplicate run → no 2nd PR; **existing Draft PR reused**; **closed PR fail-closed**; **merged PR fail-closed**; same-task commit change → conflict; **push-then-crash recovery** (no re-push); **PR-created-state writeback recovery** (no duplicate PR).
  - *Credential isolation:* token never passed to client; token absent from PR body (redacted); token absent from error text; controller never holds a token; **dry-run → zero GitHub write calls**; persistence via real `db.py` store is idempotent across controller instances.
- **Cases still missing:** live GitHub end-to-end (by design — no real token, no network); reuse of the four canonical modules (see §6 — they are absent from this worktree).

## 6. Architecture Check
- **Parallel Git implementation remains:** No — only `GitWorkspace` exists in this worktree; the canonical `repository.HostGitOperations` is on the frozen codex branch and was **not** copied in.
- **Parallel GitHub write client remains:** No — only `GitHubClient` exists in this worktree; the canonical `github_client.GitHubRestClient` is on the frozen codex branch and was **not** copied in.
- **Parallel allowlist remains:** No — a single `PROTECTED_REPOS`/allowlist exists in this worktree; the canonical `constants.py` is on the frozen codex branch.
- **Parallel SQLite registry remains:** No — the second connection/`delivery_registry` table was removed; persistence now goes through the single `db.py` store.
- **Parallel state machine remains:** No — `DeliveryState` is the only state model in this worktree; it reuses `ControlPlane`'s fail-closed discipline and error base. The canonical `constants.py` `HOST_WORKER_TERMINAL_STATES` is on the frozen codex branch.

**Why full convergence is blocked (material finding):** the task's completion criteria require actual reuse of `repository.HostGitOperations`, `github_app.GitHubAppTokenBroker`, `github_client.GitHubRestClient`, and `constants.ALLOWED_GITHUB_REPOS`. A read-only check confirmed these four modules are **absent from this worktree** and exist **only** on the frozen `codex-primary-agent-policy` branch. This task forbids modifying/touching that worktree, and copying the modules in would create the second parallel implementation the task prohibits (and risks WSL Codex coupling). Therefore criteria 2 ("existing Git / Token Broker / GitHub REST … reused") and the "no second allowlist / no second GitHub client" gates can only be **partially** met in this worktree: `db.py` / `redact.py` / `control_plane.py` are genuinely reused; the Git/GitHub/Token-Broker/constants modules are not present to reuse. The D4 Git/GitHub seams are retained as the sole implementations in **this** branch and documented as such.

## 7. Safety Confirmation
- **Real Push:** No.
- **Real PR:** No.
- **Merge:** No.
- **Protected repository accessed:** No (`yzhlx/hermes-learning-os` was only referenced as a deny constant; never cloned/read/modified).
- **Real credentials used:** No — the delivery layer never holds a token; `FakeGitHubClient`/`DbBackedDeliveryRegistry` receive none; bodies/errors redacted; tests assert zero leakage.
- **WSL Smoke:** No.
- **WSL files modified:** No.

## 8. Follow-up Commit
- **Commit created:** Yes (local only; not pushed, not in any PR).
- **Commit SHA:** `710d88e35a4402933426c7254d89117146da10d9`
- **Parent:** `a597531b083d8815046464e44c9c48308ff24f1f`
- **Title:** `D4: reconcile delivery layer onto db/redact/control_plane; harden pre-PR auth+commit binding; add diff secret scan + crash recovery`
- **Files:** `hermes_worker/delivery.py`, `hermes_worker/secret_scan.py`, `hermes_worker/db.py`, `tests/test_d4_delivery.py`, `D4-RECONCILIATION-REPORT.md`

## 9. Remaining Limitations
- The four canonical reusable modules (`repository.py`, `github_app.py`, `github_client.py`, `constants.py`) exist **only** on the frozen `codex-primary-agent-policy` branch, which this task forbids modifying or touching. Full convergence (reuse of `HostGitOperations` / `GitHubAppTokenBroker` / `GitHubRestClient` / `ALLOWED_GITHUB_REPOS`) could **not** be completed within this worktree; copying them in would create the forbidden second parallel implementation and risk WSL Codex coupling. The D4 Git/GitHub seams remain the sole implementations on **this** branch. **Recommendation to unblock:** lift the codex freeze (or port those four canonical modules onto a non-frozen branch in the main repo), then re-point `GitWorkspace`/`GitHubClient` at `HostGitOperations`/`GitHubRestClient` and delete the D4 seams — a small, mechanical step once the canonical modules are in-repo.
- No real GitHub write was executed. All GitHub writes (push / Draft PR creation / PR lookup) were verified exclusively through `FakeGitHubClient` and the dry-run path; persistence was exercised against a temp `db.py` store. The runtime end-to-end "Issue → push → Draft PR" against the live API remains **unverified** and is **not** claimed as PASS.
- Frozen commit `3612b526` remains `REVIEW_CHANGES_REQUIRED`; the WSL Codex → Docker link is intentionally **not** claimed complete (per the freeze).
- The `GhCliGitHubClient` (real write path) is implemented and gated by the authorization gate but **not invoked** this session — no live GitHub App Installation Token was used, no network call made.

## 10. Final Status
RECONCILIATION_BLOCKED
