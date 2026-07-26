"""Reconciliation tests for the Host Worker Draft PR controlled delivery layer.

Covers the D4 acceptance matrix (authorization binding task+repo+commit, protected
/ allowlist repos, git hygiene, pre-push secret scan, push-before-PR + draft
enforcement, idempotency / crash-recovery, credential isolation). All GitHub
writes go through ``FakeGitHubClient`` and persistence through either the
in-memory registry or the real ``db.py`` store — no real network, no real token.
"""
import os
import tempfile
import unittest

from hermes_worker.delivery import (
    DeliveryController,
    DeliveryAuthorization,
    DeliveryState,
    FakeGitHubClient,
    FakeGitWorkspace,
    InMemoryDeliveryRegistry,
    DbBackedDeliveryRegistry,
    GitHubClientError,
    AUTHORIZATION_REQUIRED,
    PROTECTED_REPOSITORY,
    REPOSITORY_NOT_ALLOWED,
    FORCE_PUSH_NOT_ALLOWED,
    WORKSPACE_MISSING,
    NOT_A_GIT_REPO,
    HEAD_NOT_EXPLICIT_COMMIT,
    WORKSPACE_DIRTY,
    COMMIT_NOT_IN_HISTORY,
    COMMIT_EMPTY,
    COMMIT_CONTAINS_CREDENTIAL,
    COMMIT_SHA_MISMATCH,
    BRANCH_INVALID,
    BRANCH_PROTECTED,
    CONFLICT_COMMIT_CHANGED,
    DUPLICATE_SUPPRESSED,
    PUSH_FAILED,
    PR_CREATION_FAILED,
    PR_CLOSED,
    PR_MERGED,
    DRY_RUN_NO_WRITE,
    COMPLETED,
)
from hermes_worker.secret_scan import scan_diff_for_secrets

REPO = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED = "yzhlx/hermes-learning-os"
TASK = "T1"
SHA = "a" * 40
OTHER_SHA = "b" * 40
FAKE_TOKEN = "ghp_" + "A" * 36  # 40 chars; recognized by redact as a PAT


def make_auth(task=TASK, repo=REPO, sha=SHA, grant=DeliveryAuthorization.REQUIRED_GRANT):
    return DeliveryAuthorization(task_id=task, repository=repo,
                                 commit_sha=sha, grant=grant)


def make_controller(git=None, github=None, registry=None, db_path=None,
                    allowed_repos=None, **kw):
    git = git or FakeGitWorkspace()
    github = github or FakeGitHubClient()
    allowed = allowed_repos if allowed_repos is not None else {REPO}
    return DeliveryController(
        github_client=github, git_workspace=git, registry=registry,
        db_path=db_path, allowed_repos=allowed, **kw)


def happy_deliver(controller, github, git=None, **over):
    """Run a deliver() that should succeed end-to-end (push + draft PR)."""
    kw = dict(task_id=TASK, repository=REPO, local_commit_sha=SHA,
              expected_sha=SHA, authorization=make_auth(),
              title="D4 test", body="body", test_summary="ok",
              security_summary="ok")
    kw.update(over)
    return controller.deliver(**kw)


class AuthorizationTests(unittest.TestCase):
    def test_missing_auth_blocks_push_and_pr(self):
        c = make_controller()
        g = c.github
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=None,
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertFalse(st.completed)
        self.assertEqual(g.push_calls, [])
        self.assertEqual(g.pr_calls, [])

    def test_task_mismatch_blocks(self):
        c = make_controller()
        st = c.deliver(task_id="OTHER", repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.github.push_calls, [])

    def test_repo_mismatch_blocks(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository="some/other", local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(repo=REPO),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.github.push_calls, [])

    def test_commit_sha_binding_blocks_on_mismatch(self):
        # Authorization bound to OTHER_SHA while delivering SHA -> fail-closed.
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA,
                       authorization=make_auth(sha=OTHER_SHA),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.github.push_calls, [])

    def test_wrong_grant_value_blocks(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA,
                       authorization=make_auth(grant="auto"),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)


class RepositoryAndGitTests(unittest.TestCase):
    def test_protected_repo_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=PROTECTED, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(repo=PROTECTED),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)
        self.assertEqual(c.github.push_calls, [])

    def test_protected_repo_rejected_even_if_added_to_allowlist(self):
        c = make_controller(allowed_repos={REPO, PROTECTED})
        st = c.deliver(task_id=TASK, repository=PROTECTED, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(repo=PROTECTED),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)

    def test_non_allowlist_repo_rejected(self):
        c = make_controller(allowed_repos={REPO})
        st = c.deliver(task_id=TASK, repository="some/rand", local_commit_sha=SHA,
                       expected_sha=SHA,
                       authorization=make_auth(repo="some/rand"),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, REPOSITORY_NOT_ALLOWED)
        self.assertEqual(c.github.push_calls, [])

    def test_dirty_workspace_rejected(self):
        c = make_controller(git=FakeGitWorkspace(_clean=False))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, WORKSPACE_DIRTY)
        self.assertEqual(c.github.push_calls, [])

    def test_detached_head_rejected(self):
        c = make_controller(git=FakeGitWorkspace(_detached=True))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, HEAD_NOT_EXPLICIT_COMMIT)
        self.assertEqual(c.github.push_calls, [])

    def test_commit_not_in_history_rejected(self):
        c = make_controller(git=FakeGitWorkspace(_in_history=False))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, COMMIT_NOT_IN_HISTORY)

    def test_empty_commit_rejected(self):
        c = make_controller(git=FakeGitWorkspace(_empty=True))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, COMMIT_EMPTY)

    def test_commit_sha_mismatch_task_record_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=OTHER_SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, COMMIT_SHA_MISMATCH)

    def test_default_branch_push_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       remote_branch="main")
        self.assertEqual(st.status_code, BRANCH_PROTECTED)
        self.assertEqual(c.github.push_calls, [])

    def test_force_push_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       force=True)
        self.assertEqual(st.status_code, FORCE_PUSH_NOT_ALLOWED)
        self.assertEqual(c.github.push_calls, [])

    def test_branch_delete_refspec_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       remote_branch=":refs/heads/x")
        self.assertEqual(st.status_code, BRANCH_INVALID)
        self.assertEqual(c.github.push_calls, [])

    def test_push_target_repo_matches_allowlist(self):
        c = make_controller()
        st = happy_deliver(c, c.github)
        self.assertTrue(st.completed)
        self.assertEqual(c.github.push_calls[0]["repository"], REPO)


class SecretScanTests(unittest.TestCase):
    def test_secret_in_diff_blocks(self):
        c = make_controller(git=FakeGitWorkspace(_diff=f"token={FAKE_TOKEN}"))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, COMMIT_CONTAINS_CREDENTIAL)
        self.assertEqual(c.github.push_calls, [])
        self.assertEqual(c.github.pr_calls, [])

    def test_raw_secret_not_in_message(self):
        c = make_controller(git=FakeGitWorkspace(_diff=f"token={FAKE_TOKEN}"))
        st = happy_deliver(c, c.github)
        self.assertNotIn("A" * 36, st.message)  # raw secret value absent
        self.assertIn("***REDACTED***", st.message)

    def test_scan_returns_redacted_summary_only(self):
        hits = scan_diff_for_secrets(f"see {FAKE_TOKEN} here")
        self.assertTrue(hits)
        for h in hits:
            self.assertNotIn("A" * 36, h)  # raw secret value absent
            self.assertIn("***REDACTED***", h)

    def test_no_secret_continues(self):
        c = make_controller(git=FakeGitWorkspace(_diff="normal change"))
        st = happy_deliver(c, c.github)
        self.assertTrue(st.completed)


class PushAndDraftPrTests(unittest.TestCase):
    def test_push_fail_no_pr(self):
        g = FakeGitHubClient(fail_push=True)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PUSH_FAILED)
        self.assertFalse(st.completed)
        self.assertEqual(g.pr_calls, [])

    def test_push_before_pr_order(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual([o[0] for o in g.order], ["push", "pr"])

    def test_draft_forced(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertTrue(st.pr_request.draft)
        self.assertTrue(g.pr_calls[0]["draft"])

    def test_non_draft_returned_blocks(self):
        g = FakeGitHubClient(return_non_draft=True)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)

    def test_base_head_order(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.pr_request.head_branch, "hermes/delivery-t1")
        self.assertEqual(st.pr_request.base_branch, "main")

    def test_pr_fail_not_completed(self):
        g = FakeGitHubClient(fail_pr=True)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)
        self.assertEqual(len(g.push_calls), 1)  # push happened, PR did not

    def test_no_merge_ever_called(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        happy_deliver(c, g)
        self.assertEqual(g.merge_calls, [])


class IdempotencyRecoveryTests(unittest.TestCase):
    def test_duplicate_run_no_second_pr(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        s1 = happy_deliver(c, g)
        s2 = happy_deliver(c, g)
        self.assertTrue(s1.completed)
        self.assertEqual(s2.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(len(g.pr_calls), 1)  # no second PR created

    def test_existing_draft_pr_reused(self):
        g = FakeGitHubClient()
        g.prs.append({"ok": True, "html_url": "https://github.com/x/pull/99",
                      "number": 99, "draft": True, "state": "open",
                      "repository": REPO, "head_branch": "hermes/delivery-t1",
                      "base_branch": "main"})
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(st.pr_number, 99)
        self.assertEqual(len(g.pr_calls), 0)  # no new PR created

    def test_closed_pr_fail_closed(self):
        g = FakeGitHubClient()
        g.prs.append({"ok": True, "html_url": "https://github.com/x/pull/7",
                      "number": 7, "draft": True, "state": "closed",
                      "repository": REPO, "head_branch": "hermes/delivery-t1",
                      "base_branch": "main"})
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CLOSED)
        self.assertFalse(st.completed)

    def test_merged_pr_fail_closed(self):
        g = FakeGitHubClient()
        g.prs.append({"ok": True, "html_url": "https://github.com/x/pull/8",
                      "number": 8, "draft": True, "state": "merged",
                      "repository": REPO, "head_branch": "hermes/delivery-t1",
                      "base_branch": "main"})
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_MERGED)
        self.assertFalse(st.completed)

    def test_commit_changed_conflict(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        happy_deliver(c, g, local_commit_sha=SHA)
        st2 = happy_deliver(c, g, local_commit_sha=OTHER_SHA,
                            authorization=make_auth(sha=OTHER_SHA),
                            expected_sha=OTHER_SHA)
        self.assertEqual(st2.status_code, CONFLICT_COMMIT_CHANGED)
        self.assertFalse(st2.completed)

    def test_push_then_crash_recovery(self):
        g = FakeGitHubClient()
        reg = InMemoryDeliveryRegistry()
        # Simulate a recorded push (crash before PR). Use the lowercased branch.
        reg.put(DeliveryController.idempotency_key(REPO, TASK, SHA,
                                                    "hermes/delivery-t1"),
                {"state": DeliveryState.PUSHED, "repository": REPO,
                 "task_id": TASK, "commit_sha": SHA,
                 "remote_branch": "hermes/delivery-t1", "pr_url": None,
                 "pr_number": None})
        c = make_controller(github=g, registry=reg)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual(len(g.push_calls), 0)  # push NOT repeated
        self.assertEqual(len(g.pr_calls), 1)    # PR created on recovery

    def test_pr_created_state_writeback_recovery(self):
        g = FakeGitHubClient()

        class FlakyRegistry(InMemoryDeliveryRegistry):
            def __init__(self):
                super().__init__()
                self._failures_left = 1  # transient write failure (once)

            def put(self, key, record):
                if record.get("state") == DeliveryState.PR_CREATED and self._failures_left > 0:
                    self._failures_left -= 1
                    raise RuntimeError("simulated DB write failure")
                super().put(key, record)

        reg = FlakyRegistry()
        c = make_controller(github=g, registry=reg)
        with self.assertRaises(RuntimeError):
            happy_deliver(c, g)  # PR created on GitHub, but local write fails
        self.assertEqual(len(g.pr_calls), 1)  # exactly one PR on GitHub
        # Retry: existing PR on GitHub is reused, no duplicate.
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual(len(g.pr_calls), 1)


class CredentialIsolationTests(unittest.TestCase):
    def test_token_never_passed_to_client(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        happy_deliver(c, g)
        for call in g.push_calls + g.pr_calls:
            self.assertNotIn("token", call)

    def test_token_not_in_pr_body(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g, body=f"see {FAKE_TOKEN} in body")
        self.assertNotIn("A" * 36, g.pr_calls[0]["body"])  # raw secret value absent
        self.assertIn("***REDACTED***", g.pr_calls[0]["body"])

    def test_token_not_in_error_text(self):
        g = FakeGitHubClient(fail_push=True,
                             push_error=f"auth failed for {FAKE_TOKEN}")
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertNotIn("A" * 36, st.message)  # raw secret value absent

    def test_controller_never_holds_token(self):
        c = make_controller()
        self.assertFalse(hasattr(c, "token"))
        self.assertFalse(hasattr(c, "github_token"))

    def test_dry_run_no_write_calls(self):
        g = FakeGitHubClient()
        c = make_controller(github=g)
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       dry_run=True)
        self.assertEqual(st.status_code, DRY_RUN_NO_WRITE)
        self.assertEqual(g.push_calls, [])
        self.assertEqual(g.pr_calls, [])


class DbBackedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "worker.db")

    def test_db_registry_reuses_db_module_and_is_idempotent(self):
        g = FakeGitHubClient()
        c = make_controller(github=g, db_path=self.db_path)
        s1 = happy_deliver(c, g)
        self.assertTrue(s1.completed)
        # A fresh controller on the SAME db file sees the prior delivery.
        g2 = FakeGitHubClient()
        c2 = make_controller(github=g2, db_path=self.db_path)
        s2 = happy_deliver(c2, g2)
        self.assertEqual(s2.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(len(g2.pr_calls), 0)


if __name__ == "__main__":
    unittest.main()
