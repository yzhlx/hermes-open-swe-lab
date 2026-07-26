"""Integration tests for the Host Worker Draft PR controlled delivery layer.

Covers the D4 acceptance matrix and the canonical-component integration:

* The four canonical components are importable and reused (no WSL/Codex files).
* Push is performed exclusively by ``HostGitOperations`` (canonical); the D4
  self-built parallel ``GitHubClient`` is gone.
* Draft PR creation + head-branch query go through the canonical GitHub client
  (``GitHubRestClient`` in prod / ``FakeGitHubRestClient`` offline).
* Tokens come from a formal ``token_provider`` (``GitHubAppTokenBroker``-backed
  in prod) and are never stored on the controller.
* All security gates, idempotency / crash-recovery, and secret isolation hold.

All GitHub writes go through ``FakeGitHubRestClient`` and all git pushes through
``FakeHostGitOperations``; persistence through the in-memory registry or the real
``db.py`` store. No real network, no real token, no real push/PR.
"""
import os
import tempfile
import unittest

from hermes_worker.delivery import (
    DeliveryController,
    DeliveryAuthorization,
    DeliveryState,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
    InMemoryDeliveryRegistry,
    DbBackedDeliveryRegistry,
    GitHubClientError,
    AUTHORIZATION_REQUIRED,
    PROTECTED_REPOSITORY,
    REPOSITORY_NOT_ALLOWED,
    FORCE_PUSH_NOT_ALLOWED,
    TOKEN_PROVIDER_REQUIRED,
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

# Canonical components (must be importable; no WSL/Codex coupling).
from hermes_worker.constants import ALLOWED_GITHUB_REPOS, PROTECTED_REPOS
from hermes_worker.repository import RepositoryPreparer, HostGitOperations
from hermes_worker.github_app import GitHubAppTokenBroker, FakeAppApiClient
from hermes_worker.github_client import GitHubRestClient, FakeGitHubClient


REPO = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED = "yzhlx/hermes-learning-os"
TASK = "T1"
SHA = "a" * 40
OTHER_SHA = "b" * 40
FAKE_TOKEN = "ghp_" + "A" * 36  # 40 chars; recognized by redact as a PAT


def make_auth(task=TASK, repo=REPO, sha=SHA, grant=DeliveryAuthorization.REQUIRED_GRANT):
    return DeliveryAuthorization(task_id=task, repository=repo,
                                 commit_sha=sha, grant=grant)


def recording_token_provider():
    calls = []

    def provider(repository: str) -> str:
        calls.append(repository)
        return "x-access-token-fake"
    provider.calls = calls
    return provider


_SENTINEL = object()


def make_controller(git=None, github=None, git_ops=None, registry=None,
                    db_path=None, allowed_repos=None, token_provider=_SENTINEL, **kw):
    git = git or FakeGitWorkspaceInspector()
    github = github or FakeGitHubRestClient()
    git_ops = git_ops or FakeHostGitOperations()
    allowed = allowed_repos if allowed_repos is not None else {REPO}
    if token_provider is _SENTINEL:
        token_provider = recording_token_provider()
    return DeliveryController(
        git=git, git_operations=git_ops, github_client=github,
        token_provider=token_provider, registry=registry, db_path=db_path,
        allowed_repos=allowed, **kw)


def happy_deliver(controller, github, git=None, **over):
    """Run a deliver() that should succeed end-to-end (push + draft PR)."""
    kw = dict(task_id=TASK, repository=REPO, local_commit_sha=SHA,
              expected_sha=SHA, authorization=make_auth(),
              title="D4 test", body="body", test_summary="ok",
              security_summary="ok")
    kw.update(over)
    return controller.deliver(**kw)


class CanonicalComponentTests(unittest.TestCase):
    """Section 十 — formal component source / no WSL-Codex coupling."""

    def test_constants_importable(self):
        self.assertIn(REPO, ALLOWED_GITHUB_REPOS)
        self.assertIn(PROTECTED, PROTECTED_REPOS)

    def test_repository_importable(self):
        self.assertTrue(issubclass(HostGitOperations, object))
        self.assertTrue(hasattr(RepositoryPreparer, "prepare"))

    def test_github_app_importable(self):
        self.assertTrue(hasattr(GitHubAppTokenBroker, "mint_installation_token"))

    def test_github_client_importable(self):
        self.assertTrue(issubclass(GitHubRestClient, object))
        self.assertTrue(issubclass(FakeGitHubClient, object))

    def test_no_codex_cli_runner(self):
        with self.assertRaises(ImportError):
            import hermes_worker.codex_cli_runner  # noqa: F401

    def test_no_codex_job_runner(self):
        with self.assertRaises(ImportError):
            import hermes_worker.codex_job_runner  # noqa: F401

    def test_d4_parallel_github_client_removed(self):
        import hermes_worker.delivery as d
        # The D4 self-built GitHubClient ABC and GhCliGitHubClient are gone.
        self.assertFalse(hasattr(d, "GitHubClient"))
        self.assertFalse(hasattr(d, "GhCliGitHubClient"))
        # The only GitHub client surface is the canonical one.
        self.assertIs(d.FakeGitHubClient, FakeGitHubClient)
        self.assertIs(d.GitHubRestClient, GitHubRestClient)

    def test_fake_github_rest_client_is_canonical_subclass(self):
        self.assertTrue(issubclass(FakeGitHubRestClient, FakeGitHubClient))


class AuthorizationTests(unittest.TestCase):
    def test_missing_auth_blocks_push_and_pr(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=None,
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertFalse(st.completed)
        self.assertEqual(c.git_ops.push_calls, [])
        self.assertEqual(c.github.pr_calls, [])

    def test_task_mismatch_blocks(self):
        c = make_controller()
        st = c.deliver(task_id="OTHER", repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_repo_mismatch_blocks(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository="some/other", local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(repo=REPO),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_commit_sha_binding_blocks_on_mismatch(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA,
                       authorization=make_auth(sha=OTHER_SHA),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_wrong_grant_value_blocks(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA,
                       authorization=make_auth(grant="auto"),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)

    def test_token_or_lease_does_not_replace_authorization(self):
        # Even with a working token provider, a missing/invalid authorization
        # must block. The token provider is never a substitute for the grant.
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=None,
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, AUTHORIZATION_REQUIRED)
        # token provider must NOT have been called without authorization.
        self.assertEqual(c.token_provider.calls, [])


class RepositoryAndGitTests(unittest.TestCase):
    def test_protected_repo_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=PROTECTED, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(repo=PROTECTED),
                       title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)
        self.assertEqual(c.git_ops.push_calls, [])

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
        self.assertEqual(c.git_ops.push_calls, [])

    def test_dirty_workspace_rejected(self):
        c = make_controller(git=FakeGitWorkspaceInspector(_clean=False))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, WORKSPACE_DIRTY)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_detached_head_rejected(self):
        c = make_controller(git=FakeGitWorkspaceInspector(_detached=True))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, HEAD_NOT_EXPLICIT_COMMIT)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_commit_not_in_history_rejected(self):
        c = make_controller(git=FakeGitWorkspaceInspector(_in_history=False))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, COMMIT_NOT_IN_HISTORY)

    def test_empty_commit_rejected(self):
        c = make_controller(git=FakeGitWorkspaceInspector(_empty=True))
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
        self.assertEqual(c.git_ops.push_calls, [])

    def test_force_push_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       force=True)
        self.assertEqual(st.status_code, FORCE_PUSH_NOT_ALLOWED)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_branch_delete_refspec_rejected(self):
        c = make_controller()
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       remote_branch=":refs/heads/x")
        self.assertEqual(st.status_code, BRANCH_INVALID)
        self.assertEqual(c.git_ops.push_calls, [])

    def test_token_provider_required_when_missing(self):
        c = make_controller(token_provider=None)
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, TOKEN_PROVIDER_REQUIRED)
        self.assertEqual(c.git_ops.push_calls, [])


class SecretScanTests(unittest.TestCase):
    def test_secret_in_diff_blocks(self):
        c = make_controller(
            git=FakeGitWorkspaceInspector(_diff=f"token={FAKE_TOKEN}"))
        st = happy_deliver(c, c.github)
        self.assertEqual(st.status_code, COMMIT_CONTAINS_CREDENTIAL)
        self.assertEqual(c.git_ops.push_calls, [])
        self.assertEqual(c.github.pr_calls, [])

    def test_raw_secret_not_in_message(self):
        c = make_controller(
            git=FakeGitWorkspaceInspector(_diff=f"token={FAKE_TOKEN}"))
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
        c = make_controller(git=FakeGitWorkspaceInspector(_diff="normal change"))
        st = happy_deliver(c, c.github)
        self.assertTrue(st.completed)


class PushAndDraftPrTests(unittest.TestCase):
    def test_push_failure_blocks_pr(self):
        g = FakeGitHubRestClient()
        ops = FakeHostGitOperations(fail_push=True)
        c = make_controller(github=g, git_ops=ops)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PUSH_FAILED)
        self.assertFalse(st.completed)
        self.assertEqual(g.pr_calls, [])  # no PR after failed push

    def test_push_then_pr_both_happen(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual(len(c.git_ops.push_calls), 1)  # push happened
        self.assertEqual(len(g.pr_calls), 1)            # PR happened (after push)

    def test_push_uses_host_git_operations(self):
        g = FakeGitHubRestClient()
        ops = FakeHostGitOperations()
        c = make_controller(github=g, git_ops=ops)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        # The push went through the canonical HostGitOperations, not a D4 client.
        self.assertIsInstance(ops, HostGitOperations)
        self.assertEqual(ops.push_calls[0]["branch"], "hermes/delivery-t1")

    def test_push_params_no_token_leak_and_no_force(self):
        g = FakeGitHubRestClient()
        ops = FakeHostGitOperations()
        c = make_controller(github=g, git_ops=ops)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        call = ops.push_calls[0]
        # Token arrives only as a parameter (formal credential path), never
        # embedded in the branch / refspec.
        self.assertTrue(call["token_present"])
        self.assertNotIn("ghp_", call["branch"])
        self.assertNotIn("token", call)  # no raw token string stored
        # No force parameter exists on HostGitOperations.push at all.
        self.assertNotIn("force", call)

    def test_draft_forced(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertTrue(st.pr_request.draft)
        self.assertTrue(g.pr_calls[0]["draft"])

    def test_non_draft_returned_blocks(self):
        g = FakeGitHubRestClient(return_non_draft=True)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)

    def test_base_head_correct(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.pr_request.head_branch, "hermes/delivery-t1")
        self.assertEqual(st.pr_request.base_branch, "main")
        # Formal client received the same base/head.
        self.assertEqual(g.pr_calls[0]["base"], "main")
        self.assertEqual(g.pr_calls[0]["branch"], "hermes/delivery-t1")

    def test_pr_failure_not_completed(self):
        g = FakeGitHubRestClient(fail_pr=True)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)
        self.assertEqual(len(c.git_ops.push_calls), 1)  # push happened, PR did not

    def test_no_merge_ever_called(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        happy_deliver(c, g)
        self.assertEqual(g.merge_calls, [])


class BrokerTokenTests(unittest.TestCase):
    """Token must come from the formal broker and never be held by the controller."""

    def test_broker_token_provider_used(self):
        broker = GitHubAppTokenBroker(
            app_id="app", installation_id="inst",
            app_api=FakeAppApiClient(clock=lambda: 0),
            jwt_signer=lambda *a: "fake-jwt",
            allowed_repos={REPO})
        provider = recording_token_provider()
        provider.real = broker.mint_installation_token
        c = make_controller(token_provider=broker.mint_installation_token)
        st = happy_deliver(c, c.github)
        self.assertTrue(st.completed)
        # The broker supplied the token; it flowed only as a local variable.
        self.assertFalse(hasattr(c, "token"))
        self.assertFalse(hasattr(c, "github_token"))

    def test_controller_never_holds_token(self):
        c = make_controller()
        self.assertFalse(hasattr(c, "token"))
        self.assertFalse(hasattr(c, "github_token"))
        happy_deliver(c, c.github)
        self.assertFalse(hasattr(c, "token"))
        self.assertFalse(hasattr(c, "github_token"))


class IdempotencyRecoveryTests(unittest.TestCase):
    def test_duplicate_run_no_second_pr(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        s1 = happy_deliver(c, g)
        s2 = happy_deliver(c, g)
        self.assertTrue(s1.completed)
        self.assertEqual(s2.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(len(g.pr_calls), 1)  # no second PR created

    def test_existing_draft_pr_reused(self):
        g = FakeGitHubRestClient()
        # Pre-create an open PR on the branch (via the formal client).
        g.create_draft_pr(repo=REPO, branch="hermes/delivery-t1", base="main",
                          title="t", body="")
        before = len(g.pr_calls)
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(len(g.pr_calls), before)  # no new PR created
        self.assertEqual(st.pr_number, 1)

    def test_closed_pr_fail_closed(self):
        g = FakeGitHubRestClient()
        pr = g.create_draft_pr(repo=REPO, branch="hermes/delivery-t1",
                               base="main", title="t", body="")
        g.prs[pr["number"]]["state"] = "closed"
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_CLOSED)
        self.assertFalse(st.completed)

    def test_merged_pr_fail_closed(self):
        g = FakeGitHubRestClient()
        pr = g.create_draft_pr(repo=REPO, branch="hermes/delivery-t1",
                               base="main", title="t", body="")
        g.prs[pr["number"]]["state"] = "merged"
        c = make_controller(github=g)
        st = happy_deliver(c, g)
        self.assertEqual(st.status_code, PR_MERGED)
        self.assertFalse(st.completed)

    def test_commit_changed_conflict(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        happy_deliver(c, g, local_commit_sha=SHA)
        # Advance the local HEAD to the new commit, then attempt to deliver it.
        c.git._head_sha = OTHER_SHA
        st2 = happy_deliver(c, g, local_commit_sha=OTHER_SHA,
                            authorization=make_auth(sha=OTHER_SHA),
                            expected_sha=OTHER_SHA)
        self.assertEqual(st2.status_code, CONFLICT_COMMIT_CHANGED)
        self.assertFalse(st2.completed)

    def test_push_then_crash_recovery(self):
        g = FakeGitHubRestClient()
        ops = FakeHostGitOperations()
        reg = InMemoryDeliveryRegistry()
        # Simulate a recorded push (crash before PR). Use the lowercased branch.
        reg.put(DeliveryController.idempotency_key(REPO, TASK, SHA,
                                                    "hermes/delivery-t1"),
                {"state": DeliveryState.PUSHED, "repository": REPO,
                 "task_id": TASK, "commit_sha": SHA,
                 "remote_branch": "hermes/delivery-t1", "pr_url": None,
                 "pr_number": None})
        c = make_controller(github=g, git_ops=ops, registry=reg)
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual(len(ops.push_calls), 0)  # push NOT repeated
        self.assertEqual(len(g.pr_calls), 1)      # PR created on recovery

    def test_pr_created_state_writeback_recovery(self):
        g = FakeGitHubRestClient()

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
        # Retry: existing PR on GitHub is reused, no duplicate; the recorded
        # PUSHED state prevents a second push.
        st = happy_deliver(c, g)
        self.assertTrue(st.completed)
        self.assertEqual(len(g.pr_calls), 1)
        self.assertEqual(len(c.git_ops.push_calls), 1)


class CredentialIsolationTests(unittest.TestCase):
    def test_token_never_stored_in_call_records(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        happy_deliver(c, g)
        for call in g.pr_calls + c.git_ops.push_calls:
            # Only a boolean flag is recorded, never the raw token value.
            self.assertNotIn("token", call)

    def test_token_not_in_pr_body(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = happy_deliver(c, g, body=f"see {FAKE_TOKEN} in body")
        self.assertNotIn("A" * 36, g.pr_calls[0]["body"])  # raw secret value absent
        self.assertIn("***REDACTED***", g.pr_calls[0]["body"])

    def test_token_not_in_error_text(self):
        ops = FakeHostGitOperations(
            fail_push=True, push_error=f"auth failed for {FAKE_TOKEN}")
        c = make_controller(git_ops=ops)
        st = happy_deliver(c, c.github)
        self.assertNotIn("A" * 36, st.message)  # raw secret value absent

    def test_token_provider_invoked_for_real_delivery(self):
        provider = recording_token_provider()
        c = make_controller(token_provider=provider)
        st = happy_deliver(c, c.github)
        self.assertTrue(st.completed)
        self.assertEqual(provider.calls, [REPO])  # token minted for the repo

    def test_dry_run_no_write_calls(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = c.deliver(task_id=TASK, repository=REPO, local_commit_sha=SHA,
                       expected_sha=SHA, authorization=make_auth(),
                       title="t", body="b", test_summary="", security_summary="",
                       dry_run=True)
        self.assertEqual(st.status_code, DRY_RUN_NO_WRITE)
        self.assertEqual(c.git_ops.push_calls, [])
        self.assertEqual(g.pr_calls, [])


class DbBackedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "worker.db")

    def test_db_registry_reuses_db_module_and_is_idempotent(self):
        g = FakeGitHubRestClient()
        c = make_controller(github=g, db_path=self.db_path)
        s1 = happy_deliver(c, g)
        self.assertTrue(s1.completed)
        # A fresh controller on the SAME db file sees the prior delivery.
        g2 = FakeGitHubRestClient()
        c2 = make_controller(github=g2, db_path=self.db_path)
        s2 = happy_deliver(c2, g2)
        self.assertEqual(s2.status_code, DUPLICATE_SUPPRESSED)
        self.assertEqual(len(g2.pr_calls), 0)


if __name__ == "__main__":
    unittest.main()
