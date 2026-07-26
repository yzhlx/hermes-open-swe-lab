"""Offline tests for the Host Worker GitHub Draft PR controlled delivery layer.

No real network, no real token, no protected repo access, no WSL Codex. All
GitHub writes go through ``FakeGitHubClient``; all git validation goes through
``FakeGitWorkspace`` (and one guarded test exercises the real ``LocalGitWorkspace``
when git is on PATH).

The protected repository ``yzhlx/hermes-learning-os`` is asserted unreachable
through every path. Credentials are asserted absent from call records, PR
bodies, and exception-derived status messages.

Run:  python -m unittest tests.test_d4_delivery -v
"""
import os
import sys
import shutil
import tempfile
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.delivery import (  # noqa: E402
    DeliveryController,
    DeliveryAuthorization,
    DeliveryState,
    FakeGitHubClient,
    FakeGitWorkspace,
    LocalGitWorkspace,
    InMemoryDeliveryRegistry,
    AUTHORIZATION_REQUIRED,
    PROTECTED_REPOSITORY,
    REPOSITORY_NOT_ALLOWED,
    WORKSPACE_DIRTY,
    COMMIT_SHA_MISMATCH,
    COMMIT_NOT_IN_HISTORY,
    BRANCH_PROTECTED,
    BRANCH_INVALID,
    FORCE_PUSH_NOT_ALLOWED,
    DRY_RUN_NO_WRITE,
    PUSH_FAILED,
    PR_CREATION_FAILED,
    CONFLICT_COMMIT_CHANGED,
    DUPLICATE_SUPPRESSED,
    COMPLETED,
)

LAB = "yzhlx/hermes-open-swe-lab"
SMOKE = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED = "yzhlx/hermes-learning-os"
ALLOWED = {LAB, SMOKE}
FAKE_TOKEN = "ghp_" + "x" * 36  # correctly-sized fake GitHub PAT (ghp_ + 36)
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


class D4DeliveryTest(unittest.TestCase):
    def _auth(self, task="T1", repo=LAB):
        return DeliveryAuthorization(
            task_id=task, repository=repo,
            grant=DeliveryAuthorization.REQUIRED_GRANT)

    def _make(self, git=None, github=None, allowed=None):
        git = git or FakeGitWorkspace()
        github = github or FakeGitHubClient()
        reg = InMemoryDeliveryRegistry()
        ctrl = DeliveryController(
            github_client=github, git_workspace=git, registry=reg,
            allowed_repos=allowed if allowed is not None else ALLOWED)
        return ctrl, github, git, reg

    def _good(self, **over):
        kw = dict(task_id="T1", repository=LAB, local_commit_sha=COMMIT_A,
                  expected_sha=COMMIT_A, authorization=self._auth(),
                  title="title", body="body", test_summary="tests ok",
                  security_summary="no creds")
        kw.update(over)
        return kw

    # ============ 1) unauthorized blocks push ============
    def test_01_unauthorized_blocks_push(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good(authorization=None))
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertFalse(st.completed)
        self.assertEqual(gh.push_calls, [])

    # ============ 2) unauthorized blocks draft PR ============
    def test_02_unauthorized_blocks_draft_pr(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good(authorization=None))
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(gh.pr_calls, [])

    # ============ 3) protected repo always rejected ============
    def test_03_protected_repo_rejected(self):
        ctrl, gh, _, _ = self._make()
        auth = DeliveryAuthorization(
            task_id="T1", repository=PROTECTED,
            grant=DeliveryAuthorization.REQUIRED_GRANT)
        st = ctrl.deliver(task_id="T1", repository=PROTECTED,
                          local_commit_sha=COMMIT_A, expected_sha=COMMIT_A,
                          authorization=auth, title="t", body="b",
                          test_summary="ts", security_summary="ss")
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)
        self.assertEqual(gh.push_calls, [])

    # ============ 3b) protected repo never allowed even if in allowlist ============
    def test_03b_protected_never_allowed_via_allowlist(self):
        ctrl, gh, _, _ = self._make(allowed={PROTECTED, LAB})
        auth = DeliveryAuthorization(
            task_id="T1", repository=PROTECTED,
            grant=DeliveryAuthorization.REQUIRED_GRANT)
        st = ctrl.deliver(task_id="T1", repository=PROTECTED,
                          local_commit_sha=COMMIT_A, expected_sha=COMMIT_A,
                          authorization=auth, title="t", body="b",
                          test_summary="ts", security_summary="ss")
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)
        self.assertEqual(gh.push_calls, [])

    # ============ 4) non-allowlist repo rejected ============
    def test_04_non_allowlist_repo_rejected(self):
        ctrl, gh, _, _ = self._make(allowed={SMOKE})
        auth = DeliveryAuthorization(
            task_id="T1", repository="yzhlx/other-repo",
            grant=DeliveryAuthorization.REQUIRED_GRANT)
        st = ctrl.deliver(task_id="T1", repository="yzhlx/other-repo",
                          local_commit_sha=COMMIT_A, expected_sha=COMMIT_A,
                          authorization=auth, title="t", body="b",
                          test_summary="ts", security_summary="ss")
        self.assertEqual(st.status_code, REPOSITORY_NOT_ALLOWED)
        self.assertEqual(gh.push_calls, [])

    # ============ 5) dirty workspace rejected ============
    def test_05_dirty_workspace_rejected(self):
        ctrl, gh, _, _ = self._make(git=FakeGitWorkspace(_clean=False))
        st = ctrl.deliver(**self._good())
        self.assertEqual(st.status_code, WORKSPACE_DIRTY)
        self.assertEqual(gh.push_calls, [])

    # ============ 6) commit SHA mismatch rejected ============
    def test_06_commit_sha_mismatch_rejected(self):
        git = FakeGitWorkspace(_in_history=True, _clean=True)
        ctrl, gh, _, _ = self._make(git=git)
        st = ctrl.deliver(**self._good(expected_sha=COMMIT_B))
        self.assertEqual(st.status_code, COMMIT_SHA_MISMATCH)
        self.assertEqual(gh.push_calls, [])

    # ============ 7) commit not in branch rejected ============
    def test_07_commit_not_in_branch_rejected(self):
        git = FakeGitWorkspace(_in_history=False)
        ctrl, gh, _, _ = self._make(git=git)
        st = ctrl.deliver(**self._good())
        self.assertEqual(st.status_code, COMMIT_NOT_IN_HISTORY)
        self.assertEqual(gh.push_calls, [])

    # ============ 8) direct push to default branch rejected ============
    def test_08_default_branch_push_rejected(self):
        ctrl, gh, _, _ = self._make()
        for br in ("main", "master"):
            st = ctrl.deliver(**self._good(remote_branch=br))
            self.assertEqual(st.status_code, BRANCH_PROTECTED)
        self.assertEqual(gh.push_calls, [])

    # ============ 8b) path-style branch injection rejected ============
    def test_08b_branch_injection_rejected(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good(remote_branch="feature/../../main"))
        self.assertEqual(st.status_code, BRANCH_INVALID)
        self.assertEqual(gh.push_calls, [])

    # ============ 9) force push rejected ============
    def test_09_force_push_rejected(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good(force=True))
        self.assertEqual(st.status_code, FORCE_PUSH_NOT_ALLOWED)
        self.assertEqual(gh.push_calls, [])

    # ============ 10) draft=true forced ============
    def test_10_draft_pr_forces_draft_true(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good())
        self.assertTrue(st.completed)
        self.assertTrue(st.pr_request.draft)
        self.assertTrue(gh.pr_calls[0]["draft"])
        self.assertEqual(st.pr_request.head_branch, "hermes/delivery-t1")

    # ============ 11) duplicate task does not create a second PR ============
    def test_11_duplicate_task_no_second_pr(self):
        ctrl, gh, _, _ = self._make()
        st1 = ctrl.deliver(**self._good())
        st2 = ctrl.deliver(**self._good())
        self.assertEqual(len(gh.pr_calls), 1)
        self.assertTrue(st2.completed)
        self.assertEqual(st2.status_code, DUPLICATE_SUPPRESSED)

    # ============ 12) same task, different commit -> conflict ============
    def test_12_same_task_commit_change_conflict(self):
        ctrl, gh, _, _ = self._make()
        ctrl.deliver(**self._good())                     # commit A
        st2 = ctrl.deliver(**self._good(local_commit_sha=COMMIT_B,
                                        expected_sha=COMMIT_B))  # commit B
        self.assertEqual(st2.status_code, CONFLICT_COMMIT_CHANGED)
        self.assertEqual(len(gh.pr_calls), 1)            # only first PR created
        self.assertFalse(st2.completed)

    # ============ 13) push failure -> no PR ============
    def test_13_push_failure_no_pr(self):
        ctrl, gh, _, _ = self._make(github=FakeGitHubClient(fail_push=True))
        st = ctrl.deliver(**self._good())
        self.assertEqual(st.status_code, PUSH_FAILED)
        self.assertEqual(gh.pr_calls, [])
        self.assertFalse(st.completed)

    # ============ 14) PR failure -> not marked completed ============
    def test_14_pr_failure_not_completed(self):
        ctrl, gh, _, _ = self._make(github=FakeGitHubClient(fail_pr=True))
        st = ctrl.deliver(**self._good())
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)
        self.assertNotEqual(st.state, DeliveryState.COMPLETED)
        self.assertEqual(len(gh.push_calls), 1)  # push happened, PR did not

    # ============ 15) credentials never leak ============
    def test_15_credentials_not_leaked(self):
        ctrl, gh, _, _ = self._make()
        # (a) body containing a token must be redacted in the request
        st = ctrl.deliver(**self._good(body=f"see {FAKE_TOKEN} here"))
        self.assertNotIn(FAKE_TOKEN, st.pr_request.body)
        self.assertNotIn(FAKE_TOKEN, gh.pr_calls[0]["body"])
        self.assertIn("***REDACTED***", st.pr_request.body)
        # (b) a GitHub client error containing a token must be redacted in msg
        gh2 = FakeGitHubClient(fail_push=True, push_error=f"boom {FAKE_TOKEN}")
        ctrl2, gh2, _, _ = self._make(github=gh2)
        st2 = ctrl2.deliver(**self._good())
        self.assertNotIn(FAKE_TOKEN, st2.message)
        self.assertIn("***REDACTED***", st2.message)
        # (c) token never appears in any recorded call arguments
        self.assertNotIn(FAKE_TOKEN, repr(gh.push_calls) + repr(gh.pr_calls))

    # ============ 16) dry-run produces no GitHub writes ============
    def test_16_dry_run_no_github_write(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good(dry_run=True))
        self.assertEqual(st.status_code, DRY_RUN_NO_WRITE)
        self.assertEqual(gh.push_calls, [])
        self.assertEqual(gh.pr_calls, [])
        self.assertTrue(st.dry_run)
        self.assertIsNotNone(st.push_plan)
        self.assertIsNotNone(st.pr_request)
        self.assertFalse(st.push_plan.force)

    # ============ 17) authorized path calls push then draft PR ============
    def test_17_authorized_order_push_then_pr(self):
        ctrl, gh, _, _ = self._make()
        st = ctrl.deliver(**self._good())
        self.assertTrue(st.completed)
        self.assertEqual(len(gh.push_calls), 1)
        self.assertEqual(len(gh.pr_calls), 1)
        self.assertEqual(gh.order[0][0], "push")
        self.assertEqual(gh.order[1][0], "pr")
        # push plan + pr request both present and force-free
        self.assertFalse(st.push_plan.force)
        self.assertTrue(st.pr_request.draft)

    # ============ 18) auto-merge is never called ============
    def test_18_auto_merge_never_called(self):
        ctrl, gh, _, _ = self._make()
        ctrl.deliver(**self._good())
        self.assertEqual(gh.merge_calls, [])

    # ============ integrity: idempotency key stable + commit-sensitive ============
    def test_19_idempotency_key_stable(self):
        k1 = DeliveryController.idempotency_key(LAB, "T1", COMMIT_A,
                                                "hermes/delivery-t1")
        k2 = DeliveryController.idempotency_key(LAB, "T1", COMMIT_A,
                                                "hermes/delivery-t1")
        k3 = DeliveryController.idempotency_key(LAB, "T1", COMMIT_B,
                                                "hermes/delivery-t1")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    # ============ integrity: derived branch from task id ============
    def test_20_derived_branch_from_task(self):
        ctrl, _, _, _ = self._make()
        self.assertEqual(ctrl.derive_remote_branch(task_id="T1"),
                         "hermes/delivery-t1")
        self.assertEqual(ctrl.derive_remote_branch(issue_number=42),
                         "hermes/delivery-issue-42")

    # ============ integration: real git workspace via Fake client ============
    def test_21_real_git_workspace_validates(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                           check=True)
            subprocess.run(["git", "-C", d, "config", "user.name", "t"],
                           check=True)
            ws = LocalGitWorkspace(d)
            self.assertTrue(ws.is_git_repo())
            # first commit
            with open(os.path.join(d, "f.txt"), "w") as f:
                f.write("hi")
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                           check=True)
            sha = ws.get_head_sha()
            self.assertTrue(ws.is_clean())
            self.assertTrue(ws.commit_in_history(sha))
            self.assertFalse(ws.is_empty_commit(sha))
            self.assertFalse(ws.commit_contains_credential(sha))
            # untracked -> dirty
            with open(os.path.join(d, "g.txt"), "w") as f:
                f.write("x")
            self.assertFalse(ws.is_clean())
            # credential commit
            with open(os.path.join(d, "f.txt"), "w") as f:
                f.write("token = " + FAKE_TOKEN + "\n")
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", "leak"],
                           check=True)
            self.assertTrue(ws.commit_contains_credential(ws.get_head_sha()))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_22_controller_with_real_git_workspace(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                           check=True)
            subprocess.run(["git", "-C", d, "config", "user.name", "t"],
                           check=True)
            with open(os.path.join(d, "f.txt"), "w") as f:
                f.write("hi")
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                           check=True)
            ws = LocalGitWorkspace(d)
            sha = ws.get_head_sha()
            gh = FakeGitHubClient()
            ctrl = DeliveryController(github_client=gh, git_workspace=ws,
                                      allowed_repos=ALLOWED)
            st = ctrl.deliver(task_id="T1", repository=LAB,
                              local_commit_sha=sha, expected_sha=sha,
                              authorization=self._auth(), title="t", body="b",
                              test_summary="ts", security_summary="ss")
            self.assertTrue(st.completed)
            self.assertEqual(len(gh.pr_calls), 1)
            self.assertTrue(st.pr_request.draft)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
