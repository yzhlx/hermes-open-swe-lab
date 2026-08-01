"""PB-8 scheduler, reviewer, and acceptance-lifecycle gap coverage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hermes_worker.constants as constants
from hermes_worker.agent_runner import AgentEvidence
from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.delivery import FakeGitHubRestClient
from hermes_worker.echo_sandbox import EchoSandboxBackend
from hermes_worker.reviewer import ReviewFinding, Reviewer, ReviewVerdict
from hermes_worker.scheduler import D3Orchestrator, Scheduler


REPO = "yzhlx/hermes-open-swe-smoke-test"
WORKER_TOKEN = "pb8-lifecycle-worker"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


class ApprovingReviewer(Reviewer):
    def __init__(self):
        super().__init__(decide=lambda _pr, _evidence: ReviewVerdict("APPROVE"))
        self.calls = 0

    def review(self, pr_state, evidence):
        self.calls += 1
        return super().review(pr_state, evidence)


class RequestChangesReviewer(Reviewer):
    def __init__(self):
        super().__init__(decide=lambda _pr, _evidence: ReviewVerdict(
            "REQUEST_CHANGES",
            findings=[ReviewFinding("blocking", "PB-8 deterministic finding")],
        ))
        self.calls = 0

    def review(self, pr_state, evidence):
        self.calls += 1
        return super().review(pr_state, evidence)


class CountingNonDeliveringAgent:
    """Returns non-real SHAs so the orchestrator can exercise review only."""

    def __init__(self):
        self.calls = 0

    def run(self, _sandbox, _repo_dir, _instruction, round=1,
            evidence_collector=None):
        self.calls += 1
        evidence = AgentEvidence(
            commit_sha=f"not-a-real-sha-round-{round}",
            modified_files=[], token_usage={}, model="pb8", tool_calls=0,
        )
        if evidence_collector:
            evidence_collector(evidence)
        return evidence


class PB8LifecycleGapTests(unittest.TestCase):
    def setUp(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.cp = ControlPlane(self.db_path)
        self.cp.register(WORKER_TOKEN)
        self.github = FakeGitHubRestClient()

    def tearDown(self):
        try:
            self.cp.conn.close()
        except Exception:
            pass
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + ext)
            except OSError:
                pass

    def _job_with_pr(self, *, role=constants.ROLE_CODING_AGENT,
                     commit_sha=OLD_SHA, round_number=1):
        job_id = self.cp.create_job({"command": "review"}, repo=REPO, role=role)
        self.cp.claim(WORKER_TOKEN)
        pr = self.github.create_draft_pr(
            repo=REPO, branch=f"hermes/task-{job_id}", base="main",
            title="PB-8 lifecycle test", body="", token="test-token",
        )
        self.cp.update_job(job_id, pr_number=pr["number"],
                           commit_sha=commit_sha, round=round_number)
        return job_id, pr["number"]

    def _event_types(self, job_id):
        return [event["event_type"] for event in self.cp.get_events(job_id)]

    def test_old_head_review_invalidation_documents_current_gap(self):
        """Gap 3 (documented current behavior): stale PR heads are approved.

        ``Scheduler.review_phase`` currently does not compare the PR head with
        the reviewed ``commit_sha``.  This passing characterization test records
        that it sends an APPROVE result to ``await_user`` instead of rejecting
        the stale review and requiring a re-review.
        """
        job_id, pr_number = self._job_with_pr(commit_sha=OLD_SHA)
        self.github.prs[pr_number]["head_sha"] = NEW_SHA
        reviewer = ApprovingReviewer()

        signal = Scheduler(self.cp, self.github, REPO).review_phase(
            job_id, "success", reviewer)

        self.assertNotEqual(self.github.get_pr(pr_number)["head_sha"],
                            self.cp.get_job(job_id)["commit_sha"])
        self.assertEqual(reviewer.calls, 1)
        self.assertEqual(signal["action"], "await_user")
        self.assertEqual(self.cp.get_job(job_id)["state"], "await_user")

    def test_review_under_coding_agent_role_documents_missing_runtime_binding(self):
        """Gap 5 (documented current behavior): role binding is not enforced.

        The Reviewer reports ``ROLE_REVIEWER``, but Scheduler currently accepts a
        review for a job whose persisted role is ``ROLE_CODING_AGENT``.  The
        required runtime rejection is deliberately not simulated here.
        """
        job_id, _ = self._job_with_pr(role=constants.ROLE_CODING_AGENT)
        reviewer = ApprovingReviewer()

        signal = Scheduler(self.cp, self.github, REPO).review_phase(
            job_id, "success", reviewer)

        self.assertEqual(self.cp.get_job(job_id)["role"], constants.ROLE_CODING_AGENT)
        self.assertEqual(signal["verdict"].role, constants.ROLE_REVIEWER)
        self.assertEqual(signal["action"], "await_user")

    def test_final_acceptance_gating_blocks_task_completed_before_human_accepts(self):
        """Gap 6: ``TASK_COMPLETED`` cannot be reached before FINAL_ACCEPTED."""
        job_id = self.cp.create_job({"command": "complete"}, role="coding_agent")
        self.cp.claim(WORKER_TOKEN)
        self.cp.store_agent_result(job_id, {"ci_status": "success"})
        self.cp.request_final_acceptance(WORKER_TOKEN, job_id)

        with self.assertRaises(ControlPlaneError) as raised:
            self.cp.complete(WORKER_TOKEN, job_id, {"exit_code": 0})

        self.assertEqual(raised.exception.args[0], "completion_before_acceptance")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(job_id))
        self.assertNotEqual(self.cp.get_job(job_id)["state"], "completed")

    def test_max_rounds_two_request_changes_escalates_with_user_action(self):
        """Gap 7 (closed by PB-7): two blocking reviews stop at MAX_ROUNDS and
        raise ``USER_ACTION_REQUIRED`` / ``TASK_BLOCKED``.

        After PB-7, the Scheduler moves to ``escalated`` after two
        REQUEST_CHANGES AND records the required ``USER_ACTION_REQUIRED``
        event with reason ``TASK_BLOCKED`` via the control plane.  This test
        asserts the closed behavior (supersedes the PB-1..PB-6-era
        ``currently_without_user_action`` variant).
        """
        job_id, _ = self._job_with_pr(round_number=1)
        reviewer = RequestChangesReviewer()
        scheduler = Scheduler(self.cp, self.github, REPO)

        first = scheduler.review_phase(job_id, "success", reviewer)
        second = scheduler.review_phase(job_id, "success", reviewer)

        self.assertEqual(first["action"], "rework")
        self.assertEqual(first["round"], constants.MAX_ROUNDS)
        self.assertEqual(second["action"], "escalated")
        self.assertEqual(reviewer.calls, constants.MAX_ROUNDS)
        self.assertEqual(self.cp.get_job(job_id)["state"], "escalated")
        self.assertIn("escalated", self._event_types(job_id))
        # PB-7: USER_ACTION_REQUIRED(TASK_BLOCKED) is emitted on escalation.
        uar_events = [e for e in self.cp.get_events(job_id)
                      if e.get("event_type") == constants.USER_ACTION_REQUIRED]
        self.assertEqual(len(uar_events), 1)
        self.assertEqual(json.loads(uar_events[0]["payload"])["reason"],
                         constants.TASK_BLOCKED)

    def test_failure_escalation_stops_orchestrator_after_max_rounds(self):
        """Gap 8: the loop stops at MAX_ROUNDS; Human Owner notice is missing.

        This deterministic orchestration run verifies no third coding phase is
        scheduled.  The existing implementation returns ``escalated`` rather
        than a Human-Owner ``USER_ACTION_REQUIRED`` event, which is documented
        as the remaining failure-escalation gap.
        """
        job_id = self.cp.create_job({"command": "rework"}, repo=REPO,
                                    role=constants.ROLE_CODING_AGENT)
        agent = CountingNonDeliveringAgent()
        reviewer = RequestChangesReviewer()
        orchestrator = D3Orchestrator(
            self.cp, self.github, None, agent, reviewer, EchoSandboxBackend(),
            REPO, release_agent=object(),
        )

        signal = orchestrator.run_job(job_id, WORKER_TOKEN, "rework")

        self.assertEqual(signal["action"], "escalated")
        self.assertEqual(agent.calls, constants.MAX_ROUNDS)
        self.assertEqual(reviewer.calls, constants.MAX_ROUNDS)
        self.assertEqual(self.cp.get_job(job_id)["state"], "escalated")
        self.assertNotEqual(self.cp.get_job(job_id)["state"],
                            constants.USER_ACTION_REQUIRED)

    def test_scheduler_restart_resumes_persisted_pr_review_without_new_job(self):
        """Gap 9: a restarted Scheduler resumes a persisted PR review.

        This is a scheduler-level crash/restart characterization: after the
        original ControlPlane connection is closed, a fresh process object reads
        the same job/PR data and completes the pending review without creating a
        replacement task or PR.
        """
        job_id, pr_number = self._job_with_pr()
        self.cp.set_state(job_id, "agent_done")
        self.cp.conn.close()
        restarted = ControlPlane(self.db_path)
        try:
            reviewer = ApprovingReviewer()
            signal = Scheduler(restarted, self.github, REPO).review_phase(
                job_id, "success", reviewer)
            self.assertEqual(signal["action"], "await_user")
            self.assertEqual(restarted.get_job(job_id)["pr_number"], pr_number)
            self.assertEqual(len(restarted.list_jobs()), 1)
            self.assertEqual(self.github.pr_numbers(), [pr_number])
        finally:
            restarted.conn.close()


if __name__ == "__main__":
    unittest.main()
