"""Canonical Phase B baseline integration tests (dry-run, local only).

These tests pin the *canonical* invariants that must survive the PR #5
(d3-integration) + PR #6 (d4-delivery-layer) merge, independent of the
pre-existing per-module suites. They deliberately avoid adding any Phase B
scheduler / state-machine / reviewer / USER_ACTION / FINAL_ACCEPTANCE
implementation -- this file is assertions only.

Run from the working tree root so ``hermes_worker`` is importable:
    python -m unittest tests.test_canonical_baseline_integration -v
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

# Make hermes_worker importable when run as a module from the working tree root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hermes_worker.control_plane import ControlPlane                 # req 1
import hermes_worker.delivery as delivery_mod                        # req 2
from hermes_worker.delivery import (
    DeliveryController,
    DeliveryAuthorization,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
    GitHubClientError,
    PROTECTED_REPOSITORY,
    PR_CREATION_FAILED,
)
from hermes_worker.db import init_db                                # req 3/4
from hermes_worker.secret_scan import scan_diff_for_secrets         # req 11
from hermes_worker import secret_scan as _secret_scan_mod
from hermes_worker.constants import ALLOWED_GITHUB_REPOS, PROTECTED_REPOS
from hermes_worker.repository import RepositoryPreparer, HostGitOperations
from hermes_worker.github_app import GitHubAppTokenBroker, FakeAppApiClient
from hermes_worker.github_client import (
    GitHubClient,
    FakeGitHubClient,
    RealGitHubClient,
    GitHubRestClient,
)


REPO = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED = "yzhlx/hermes-learning-os"
TASK = "T1"
SHA = "a" * 40
FAKE_TOKEN = "ghp_" + "A" * 36  # 40 chars; recognized by redact as a PAT


def make_auth(task=TASK, repo=REPO, sha=SHA):
    return DeliveryAuthorization(
        task_id=task, repository=repo, commit_sha=sha,
        grant=DeliveryAuthorization.REQUIRED_GRANT,
    )


def make_controller(github=None, git_ops=None, allowed_repos=None):
    github = github or FakeGitHubRestClient()
    git_ops = git_ops or FakeHostGitOperations()
    allowed = allowed_repos if allowed_repos is not None else {REPO}
    return DeliveryController(
        git=FakeGitWorkspaceInspector(),
        git_operations=git_ops,
        github_client=github,
        token_provider=lambda repository: "x-access-token-fake",
        registry=None,
        allowed_repos=allowed,
    )


def happy_deliver(controller, **over):
    kw = dict(
        task_id=TASK, repository=REPO, local_commit_sha=SHA,
        expected_sha=SHA, authorization=make_auth(),
        title="D4 test", body="body", test_summary="ok",
        security_summary="ok",
    )
    kw.update(over)
    return controller.deliver(**kw)


class CanonicalBaselineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._dbs = []

    def tearDown(self):
        for p in self._dbs:
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(p + ext)
                except OSError:
                    pass

    def _tmp_db(self):
        p = tempfile.mktemp(suffix=".db")
        self._dbs.append(p)
        return p

    # ---- Req 1: D3 control-plane module still importable ------------------
    def test_01_d3_control_plane_importable(self):
        self.assertTrue(hasattr(ControlPlane, "claim"))
        self.assertTrue(hasattr(ControlPlane, "append_event"))

    # ---- Req 2: D4 delivery module still importable -----------------------
    def test_02_d4_delivery_importable(self):
        self.assertTrue(hasattr(delivery_mod, "DeliveryController"))
        self.assertTrue(hasattr(delivery_mod, "DeliveryAuthorization"))

    # ---- Req 3: DB init idempotent on the same temp sqlite ----------------
    def test_03_db_init_idempotent(self):
        p = self._tmp_db()
        c1 = init_db(p)
        c2 = init_db(p)  # second call on the same file must not raise
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        c1.close()
        c2.close()

    # ---- Req 4: D3 issue_tasks + D4 delivery_state coexist ----------------
    def test_04_d3_and_d4_tables_exist(self):
        p = self._tmp_db()
        conn = init_db(p)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('issue_tasks','delivery_state')"
        ).fetchall()
        names = {r["name"] for r in rows}
        self.assertIn("issue_tasks", names)
        self.assertIn("delivery_state", names)
        conn.close()

    # ---- Req 5: protected repo still rejected -----------------------------
    def test_05_protected_repo_rejected(self):
        self.assertIn(PROTECTED, PROTECTED_REPOS)
        c = make_controller()
        st = c.deliver(
            task_id=TASK, repository=PROTECTED, local_commit_sha=SHA,
            expected_sha=SHA, authorization=make_auth(repo=PROTECTED),
            title="t", body="b", test_summary="", security_summary="",
        )
        self.assertEqual(st.status_code, PROTECTED_REPOSITORY)
        self.assertEqual(c.git_ops.push_calls, [])

    # ---- Req 6: non-Draft PR still rejected -------------------------------
    def test_06_non_draft_pr_rejected(self):
        g = FakeGitHubRestClient(return_non_draft=True)
        c = make_controller(github=g)
        st = happy_deliver(c)
        self.assertEqual(st.status_code, PR_CREATION_FAILED)
        self.assertFalse(st.completed)

    # ---- Req 7: auto-merge unreachable ------------------------------------
    def test_07_auto_merge_unreachable(self):
        # DeliveryController exposes no merge entry point.
        self.assertFalse(hasattr(DeliveryController, "merge"))
        self.assertFalse(hasattr(DeliveryController, "merge_pr"))
        # RealGitHubClient.merge_pr fails closed.
        with self.assertRaises(GitHubClientError):
            RealGitHubClient().merge_pr(1)
        # A successful deliver never invokes merge.
        g = FakeGitHubRestClient()
        c = make_controller(github=g)
        st = happy_deliver(c)
        self.assertTrue(st.completed)
        self.assertEqual(g.merge_calls, [])

    # ---- Req 8: exactly one canonical push/Draft-PR path ------------------
    def test_08_single_canonical_push_pr_path(self):
        hw_dir = os.path.join(_ROOT, "hermes_worker")
        call_files = set()
        repo_has_push = False
        for fn in os.listdir(hw_dir):
            if not fn.endswith(".py") or fn == "__init__.py":
                continue
            path = os.path.join(hw_dir, fn)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            # Production *call* sites of push_branch / create_draft_pr.
            if re.search(r"\.(push_branch|create_draft_pr)\s*\(", text):
                call_files.add(fn)
            if fn == "repository.py" and re.search(r"def push\s*\(", text):
                repo_has_push = True
        # Only delivery.py (controlled DeliveryController) and scheduler.py
        # (legacy, fail-closed in prod via RealGitHubClient) reference these
        # methods in production code -- no second controller exists.
        self.assertTrue(
            call_files.issubset({"delivery.py", "scheduler.py"}),
            f"unexpected push/create_draft_pr call sites: {sorted(call_files)}",
        )
        # The canonical git-push implementation lives in repository.py.
        self.assertTrue(repo_has_push)

    # ---- Req 9: PR #5 GitHub client interfaces not lost -------------------
    def test_09_pr5_github_client_readonly_interfaces_present(self):
        for method in ("get_pr", "get_ci_status", "add_label"):
            self.assertTrue(
                hasattr(GitHubClient, method),
                f"GitHubClient.{method} missing (PR #5 interface lost)",
            )
            self.assertTrue(callable(getattr(GitHubClient, method)))
            self.assertTrue(hasattr(FakeGitHubClient, method))
            self.assertTrue(callable(getattr(FakeGitHubClient, method)))

    # ---- Req 10: D4 token broker interface not lost -----------------------
    def test_10_d4_token_broker_interface_present(self):
        self.assertTrue(hasattr(GitHubAppTokenBroker, "get_token_for_job"))
        self.assertTrue(callable(GitHubAppTokenBroker.get_token_for_job))

    # ---- Req 11: secret scan still effective ------------------------------
    def test_11_secret_scan_rejects_fake_secret(self):
        self.assertTrue(hasattr(_secret_scan_mod, "scan_diff_for_secrets"))
        self.assertEqual(scan_diff_for_secrets(""), [])
        self.assertEqual(scan_diff_for_secrets("normal change"), [])
        hits = scan_diff_for_secrets("token=" + FAKE_TOKEN)
        self.assertTrue(hits, "fake secret should be detected and rejected")

    # ---- Req 12: all error paths fail-closed ------------------------------
    def test_12_error_paths_fail_closed(self):
        rgc = RealGitHubClient()
        with self.assertRaises(GitHubClientError):
            rgc.push_branch(REPO, "hermes/x", SHA)
        with self.assertRaises(GitHubClientError):
            rgc.create_draft_pr(REPO, "hermes/x", "title")


if __name__ == "__main__":
    unittest.main(verbosity=2)
