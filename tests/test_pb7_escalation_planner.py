"""PB-7 regression coverage for escalation and issue-task dispatch."""
import json
import os
import tempfile
import unittest

from hermes_worker import constants
from hermes_worker.control_plane import ControlPlane
from hermes_worker.reviewer import ReviewFinding, ReviewVerdict, Reviewer
from hermes_worker.scheduler import Scheduler


REPO = "yzhlx/hermes-open-swe-smoke-test"


class _GitHubStub:
    """Only the Scheduler's read/label surface needed by these tests."""

    def __init__(self):
        self.labels = []

    def get_pr(self, pr_number):
        return {"number": pr_number}

    def add_label(self, pr_number, label):
        self.labels.append((pr_number, label))


class PB7EscalationPlannerTest(unittest.TestCase):
    def setUp(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.cp = ControlPlane(self.db_path)
        self.github = _GitHubStub()
        self.scheduler = Scheduler(self.cp, self.github, REPO)
        self.blocking_reviewer = Reviewer(
            decide=lambda _pr, _evidence: ReviewVerdict(
                "REQUEST_CHANGES",
                findings=[ReviewFinding("blocking", "needs rework")],
            )
        )

    def tearDown(self):
        self.cp.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def _new_reviewable_job(self, *, round_number):
        job_id, _ = self.cp.create_issue_task(
            REPO, 700 + round_number, {"instruction": "PB-7"},
            role=constants.ROLE_CODING_AGENT,
        )
        self.cp.update_job(job_id, pr_number=71, round=round_number)
        return job_id

    def _events_of_type(self, job_id, event_type):
        return [event for event in self.cp.get_events(job_id)
                if event["event_type"] == event_type]

    def test_over_max_rounds_requests_user_action_with_task_blocked_reason(self):
        job_id = self._new_reviewable_job(round_number=constants.MAX_ROUNDS)

        signal = self.scheduler.review_phase(
            job_id, "success", self.blocking_reviewer)

        self.assertEqual(signal["action"], "escalated")
        self.assertEqual(self.cp.get_job(job_id)["state"], "escalated")
        user_actions = self._events_of_type(
            job_id, constants.USER_ACTION_REQUIRED)
        self.assertEqual(len(user_actions), 1)
        self.assertEqual(json.loads(user_actions[0]["payload"])["reason"],
                         constants.TASK_BLOCKED)
        self.assertEqual(len(self._events_of_type(job_id, "escalated")), 1)

    def test_repeated_escalation_is_deduplicated(self):
        job_id = self._new_reviewable_job(round_number=constants.MAX_ROUNDS)

        self.scheduler.review_phase(job_id, "success", self.blocking_reviewer)
        self.scheduler.review_phase(job_id, "success", self.blocking_reviewer)

        self.assertEqual(len(self._events_of_type(
            job_id, constants.USER_ACTION_REQUIRED)), 1)
        self.assertEqual(len(self._events_of_type(job_id, "escalated")), 1)
        self.assertEqual(self.cp.get_job(job_id)["state"], "escalated")

    def test_dispatch_creates_exactly_one_task_per_issue(self):
        first_id, first_created = self.cp.create_issue_task(
            REPO, 801, {"instruction": "first"}, role=constants.ROLE_CODING_AGENT)
        second_id, second_created = self.cp.create_issue_task(
            REPO, 801, {"instruction": "retry"}, role=constants.ROLE_CODING_AGENT)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(second_id, first_id)
        self.assertEqual(len(self.cp.list_jobs()), 1)
        self.assertEqual(self.cp.get_job_by_issue(REPO, 801), first_id)

    def test_rework_before_max_rounds_does_not_request_user_action(self):
        job_id = self._new_reviewable_job(round_number=constants.MAX_ROUNDS - 1)

        signal = self.scheduler.review_phase(
            job_id, "success", self.blocking_reviewer)

        self.assertEqual(signal["action"], "rework")
        self.assertEqual(signal["round"], constants.MAX_ROUNDS)
        self.assertEqual(self._events_of_type(
            job_id, constants.USER_ACTION_REQUIRED), [])
        self.assertEqual(self._events_of_type(job_id, "escalated"), [])
        self.assertEqual(self.github.labels, [(71, constants.ROUND2_LABEL)])


if __name__ == "__main__":
    unittest.main()
