"""PB-8 delivery-path gap coverage (offline and deterministic)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hermes_worker.control_plane import ControlPlane
from hermes_worker.delivery import (
    COMPLETED,
    PUSH_FAILED,
    DeliveryAuthorization,
    DeliveryController,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
)
from hermes_worker.release_delivery_coordinator import ReleaseAgent


REPO = "yzhlx/hermes-open-swe-smoke-test"
SHA = "a" * 40


def _auth(task_id: str) -> DeliveryAuthorization:
    return DeliveryAuthorization(
        task_id=task_id,
        repository=REPO,
        commit_sha=SHA,
        grant=DeliveryAuthorization.REQUIRED_GRANT,
    )


class BarrierGitHubClient(FakeGitHubRestClient):
    """Force two calls past the head lookup before either PR is created."""

    def __init__(self):
        super().__init__()
        self.create_barrier = threading.Barrier(2, timeout=5)

    def create_draft_pr(self, *args, **kwargs):
        self.create_barrier.wait()
        return super().create_draft_pr(*args, **kwargs)


class PB8DeliveryGapTests(unittest.TestCase):
    def setUp(self):
        self._db_path = tempfile.mktemp(suffix=".db")
        self.cp = ControlPlane(self._db_path)

    def tearDown(self):
        self.cp.conn.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_path + ext)
            except OSError:
                pass

    @staticmethod
    def _controller(github, git_ops):
        return DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=git_ops,
            github_client=github,
            token_provider=lambda _repo: "test-installation-token",
            allowed_repos={REPO},
        )

    @staticmethod
    def _deliver_kwargs(task_id: str):
        return {
            "task_id": task_id,
            "repository": REPO,
            "local_commit_sha": SHA,
            "expected_sha": SHA,
            "authorization": _auth(task_id),
            "title": "PB-8 delivery gap test",
            "body": "offline test",
            "test_summary": "passed",
            "security_summary": "none",
        }

    def test_event_write_success_then_push_failure_has_no_orphan_pr_on_retry(self):
        """Gap 11: a recorded failed delivery is retried without an orphan PR.

        The first call persists the Release Agent's ``delivery_blocked`` event
        after the push failure.  Retrying the same task creates one Draft PR,
        rather than treating that persisted event as a false delivery success.
        """
        github = FakeGitHubRestClient()
        git_ops = FakeHostGitOperations(fail_push=True)
        release = ReleaseAgent(self.cp, self._controller(github, git_ops), REPO)
        job_id = self.cp.create_job({"command": "delivery"})
        release_kwargs = self._deliver_kwargs("g11")
        commit_sha = release_kwargs.pop("local_commit_sha")
        release_kwargs.pop("authorization")

        first = release.deliver_task(
            job_id=job_id, commit_sha=commit_sha, **release_kwargs)
        self.assertEqual(first.status_code, PUSH_FAILED)
        self.assertEqual(github.pr_numbers(), [])
        self.assertEqual(
            [event["event_type"] for event in self.cp.get_events(job_id)],
            ["delivery_blocked"],
        )

        git_ops.fail_push = False
        retry = release.deliver_task(
            job_id=job_id, commit_sha=commit_sha, **release_kwargs)
        self.assertEqual(retry.status_code, COMPLETED)
        self.assertEqual(github.pr_numbers(), [1])
        self.assertEqual(len(github.pr_calls), 1)

    def test_two_delivery_paths_racing_currently_create_two_prs_documents_gap(self):
        """Gap 12 (documented current behavior): concurrent delivery is unlocked.

        The mandated target is exactly one PR for concurrent identical calls.
        ``DeliveryController`` currently has no transaction/lock around the
        lookup-create sequence.  The barrier makes both calls observe no PR, so
        this test intentionally records the current two-PR result instead of
        masking the missing synchronization with scheduling luck.
        """
        github = BarrierGitHubClient()
        controller = self._controller(github, FakeHostGitOperations())
        statuses = []
        failures = []

        def deliver():
            try:
                statuses.append(controller.deliver(**self._deliver_kwargs("g12")))
            except Exception as exc:  # assertions below preserve thread errors
                failures.append(exc)

        first = threading.Thread(target=deliver)
        second = threading.Thread(target=deliver)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(statuses), 2)
        self.assertEqual(len(github.pr_numbers()), 2)
        self.assertEqual(len(github.pr_calls), 2)


if __name__ == "__main__":
    unittest.main()
