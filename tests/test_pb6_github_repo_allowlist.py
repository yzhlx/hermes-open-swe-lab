"""PB-6 — GitHub repo allowlist + Reviewer/CI/label identity isolation.

Every gh/GitHub-backed read or write (reviewer, CI query, label) must verify
``repository in ALLOWED_GITHUB_REPOS`` (exact, normalized ``owner/repo`` match)
BEFORE any network/gh call. Unallowed or protected repos fail closed with
``repo_not_allowed``; ``RealGitHubClient`` push/create_draft_pr stay disabled.

Allowed to modify for PB-6: hermes_worker/reviewer.py, hermes_worker/github_client.py.
Forbidden: scheduler.py, delivery.py, control_plane.py, constants.py, db.py,
deployment tests, GitHub workflows, PR #9 files.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock

from hermes_worker.constants import ALLOWED_GITHUB_REPOS, PROTECTED_REPOS
from hermes_worker.github_client import (
    GitHubClientError,
    FakeGitHubClient,
    RealGitHubClient,
    normalize_repo,
    require_allowed_repo,
)
from hermes_worker.reviewer import Reviewer


ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED_REPO = "yzhlx/hermes-learning-os"
FAKE_TOKEN = "ghp_FAKE_TOKEN_SHOULD_NEVER_LEAK_ABCDEFG123456"


def _fake_run(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


class TestPB6ReviewerAllowlist(unittest.TestCase):
    def test_allowed_repo_reviewer_pass(self):
        reviewer = Reviewer(repo=ALLOWED_REPO)
        verdict = reviewer.review({"number": 1}, {"ci_status": "success"})
        self.assertEqual(verdict.verdict, "APPROVE")

    def test_unallowed_repo_reviewer_reject_zero_gh_calls(self):
        # Reviewer must refuse and must NOT reach any GitHub/gh call.
        reviewer = Reviewer(repo="yzhlx/other-repo")
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError) as exc:
                reviewer.review({"number": 1}, {"ci_status": "success"})
        run.assert_not_called()
        self.assertEqual(str(exc.exception), "repo_not_allowed")

    def test_protected_repo_reviewer_permanently_rejected(self):
        # yzhlx/hermes-learning-os must never be reviewed, even if widened.
        with self.assertRaises(GitHubClientError):
            Reviewer(repo=PROTECTED_REPO).review(
                {"number": 1}, {"ci_status": "success"})


class TestPB6CIQueryAllowlist(unittest.TestCase):
    def test_allowed_repo_ci_query_pass(self):
        client = RealGitHubClient()
        with patch("subprocess.run",
                   return_value=_fake_run(json.dumps([{"state": "SUCCESS"}]))) as run:
            status = client.get_ci_status(1, repo=ALLOWED_REPO)
        self.assertEqual(status, "success")
        run.assert_called_once()

    def test_unallowed_repo_ci_query_reject_zero_gh_calls(self):
        client = RealGitHubClient()
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError) as exc:
                client.get_ci_status(1, repo="yzhlx/other-repo")
        run.assert_not_called()
        self.assertEqual(str(exc.exception), "repo_not_allowed")

    def test_unconfigured_repo_ci_query_fail_closed(self):
        # Production fail-closed: no repo configured -> never call gh.
        client = RealGitHubClient()
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError) as exc:
                client.get_ci_status(1)  # repo omitted
        run.assert_not_called()
        self.assertEqual(str(exc.exception), "repo_not_allowed")

    def test_protected_repo_ci_query_rejected(self):
        client = RealGitHubClient()
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError):
                client.get_ci_status(1, repo=PROTECTED_REPO)
        run.assert_not_called()


class TestPB6LabelAllowlist(unittest.TestCase):
    def test_allowed_repo_label_pass(self):
        client = RealGitHubClient()
        with patch("subprocess.run", return_value=_fake_run()) as run:
            client.add_label(1, "round-2", repo=ALLOWED_REPO)
        run.assert_called_once()

    def test_unallowed_repo_label_reject_zero_gh_calls(self):
        client = RealGitHubClient()
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError) as exc:
                client.add_label(1, "round-2", repo="yzhlx/other-repo")
        run.assert_not_called()
        self.assertEqual(str(exc.exception), "repo_not_allowed")

    def test_protected_repo_label_rejected(self):
        client = RealGitHubClient()
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError):
                client.add_label(1, "x", repo=PROTECTED_REPO)
        run.assert_not_called()


class TestPB6TokenIsolation(unittest.TestCase):
    def test_token_not_in_logs_or_gh_args(self):
        # Unallowed path: no gh call, error carries no token.
        with patch("subprocess.run") as run:
            with self.assertRaises(GitHubClientError) as exc:
                RealGitHubClient().get_ci_status(1, repo="yzhlx/other-repo")
        run.assert_not_called()
        self.assertEqual(str(exc.exception), "repo_not_allowed")
        self.assertNotIn(FAKE_TOKEN, str(exc.exception))

        # Allowed path: gh is invoked with a FIXED argv that never contains a
        # token. gh uses ambient auth; our code injects no credential into the
        # command, URL, or error message, and performs no logging of secrets.
        with patch("subprocess.run",
                   return_value=_fake_run(json.dumps([{"state": "SUCCESS"}]))) as run:
            RealGitHubClient().get_ci_status(1, repo=ALLOWED_REPO)
        self.assertEqual(run.call_count, 1)
        argv = [str(a) for a in run.call_args.args[0]]
        self.assertEqual(argv, ["gh", "pr", "checks", "1", "--json", "state"])
        self.assertNotIn(FAKE_TOKEN, " ".join(argv))


class TestPB6RealClientDisabledPaths(unittest.TestCase):
    def test_push_and_create_draft_pr_still_disabled(self):
        client = RealGitHubClient()
        with self.assertRaises(GitHubClientError):
            client.push_branch(ALLOWED_REPO, "b", "sha")
        with self.assertRaises(GitHubClientError):
            client.create_draft_pr(ALLOWED_REPO, "b", "title")
        with self.assertRaises(GitHubClientError):
            client.clone(ALLOWED_REPO, "/tmp/x")

    def test_merge_pr_still_disabled(self):
        client = RealGitHubClient()
        with self.assertRaises(GitHubClientError):
            client.merge_pr(1)


class TestPB6Normalization(unittest.TestCase):
    def test_exact_match_no_prefix_attack(self):
        self.assertEqual(
            require_allowed_repo("yzhlx/hermes-open-swe-smoke-test"),
            "yzhlx/hermes-open-swe-smoke-test")
        # Prefix extension must NOT match.
        with self.assertRaises(GitHubClientError):
            require_allowed_repo("yzhlx/hermes-open-swe-smoke-test-evil")

    def test_case_and_git_suffix_normalization(self):
        self.assertEqual(
            require_allowed_repo("Yzhlx/Hermes-Open-SWE-Smoke-Test"),
            "yzhlx/hermes-open-swe-smoke-test")
        self.assertEqual(
            require_allowed_repo("yzhlx/hermes-open-swe-smoke-test.git"),
            "yzhlx/hermes-open-swe-smoke-test")

    def test_none_and_empty_fail_closed(self):
        with self.assertRaises(GitHubClientError):
            require_allowed_repo(None)
        with self.assertRaises(GitHubClientError):
            require_allowed_repo("   ")

    def test_normalize_helper(self):
        self.assertIsNone(normalize_repo(None))
        self.assertEqual(normalize_repo("  Yzhlx/X.git "), "yzhlx/x")

    def test_protected_explicit_deny_even_if_widened(self):
        # If someone mistakenly widens the allowlist, protected stays denied.
        with self.assertRaises(GitHubClientError):
            require_allowed_repo(PROTECTED_REPO,
                                 allowed={ALLOWED_REPO, PROTECTED_REPO})


class TestPB6FakeClientCompatibility(unittest.TestCase):
    def test_backward_compat_without_repo(self):
        fc = FakeGitHubClient()
        fc.set_ci_status(1, "pending")
        self.assertEqual(fc.get_ci_status(1), "pending")
        fc.add_label(1, "x")
        self.assertTrue(fc.has_label(1, "x"))

    def test_fake_client_repo_enforcement(self):
        fc = FakeGitHubClient()
        # allowed repo works
        fc.get_ci_status(1, repo=ALLOWED_REPO)
        fc.add_label(1, "y", repo=ALLOWED_REPO)
        self.assertTrue(fc.has_label(1, "y"))
        # unallowed repo rejected
        with self.assertRaises(GitHubClientError):
            fc.get_ci_status(1, repo="yzhlx/other-repo")
        with self.assertRaises(GitHubClientError):
            fc.add_label(1, "z", repo="yzhlx/other-repo")


if __name__ == "__main__":
    unittest.main()
