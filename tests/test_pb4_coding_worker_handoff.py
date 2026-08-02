"""PB-4 — Coding Worker produces a REAL local commit and hands off to ReleaseAgent.

The Coding Worker (``WorkerAgent``) must:
  * run tests, create a REAL local commit, return its real ``commit_sha``,
  * persist the commit and record COMMIT_CREATED,
  * request RELEASE_HANDOFF_REQUESTED with the SAME sha,
and must NEVER:
  * push, open a Draft PR, call DeliveryController.deliver() directly,
  * construct a DeliveryAuthorization, hold delivery credentials,
  * report a "simulated" / missing commit as a deliverable.
"""
import os
import sys
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hermes_worker.control_plane import ControlPlane
from hermes_worker.delivery import (
    DeliveryController,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
)
from hermes_worker.release_delivery_coordinator import ReleaseAgent
from hermes_worker.scheduler import (
    WorkerAgent,
    EVENT_COMMIT_CREATED,
    EVENT_RELEASE_HANDOFF_REQUESTED,
)
from hermes_worker.agent_runner import AgentRunner, AgentEvidence
from hermes_worker.echo_sandbox import EchoSandboxBackend
from hermes_worker.github_client import FakeGitHubClient

REPO = "yzhlx/hermes-open-swe-smoke-test"
TOK = "wk-pb4-test"


def _make_real_commit():
    d = tempfile.mkdtemp(prefix="pb4-commit-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"], check=True)
    sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return d, sha


class RealCommitAgentRunner(AgentRunner):
    def __init__(self, sha, model="real-agent"):
        self._sha = sha
        self.model = model

    def run(self, sandbox, repo_dir, instruction, round=1,
            evidence_collector=None):
        sandbox.write_file("automation-smoke-test/README.md",
                           f"# {instruction}\n")
        ev = AgentEvidence(
            commit_sha=self._sha,
            modified_files=["automation-smoke-test/README.md"],
            token_usage={"total": 1}, model=self.model, tool_calls=1)
        if evidence_collector:
            evidence_collector(ev)
        return ev


class FailingAgentRunner(AgentRunner):
    """Simulates a test failure: the agent raises before a deliverable commit."""

    def run(self, sandbox, repo_dir, instruction, round=1,
            evidence_collector=None):
        raise RuntimeError("agent tests failed")


class NoCommitAgentRunner(AgentRunner):
    def run(self, sandbox, repo_dir, instruction, round=1,
            evidence_collector=None):
        return AgentEvidence(commit_sha=None,
                             modified_files=[], token_usage={}, tool_calls=0)


class SimulatedAgentRunner(AgentRunner):
    def run(self, sandbox, repo_dir, instruction, round=1,
            evidence_collector=None):
        return AgentEvidence(commit_sha="simulated",
                             modified_files=[], token_usage={}, tool_calls=0)


class SpyReleaseAgent:
    """Records handoff calls and returns a fake successful result."""

    def __init__(self):
        self.calls = []

    def deliver_task(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            pr_number=42, pr_url="https://github.com/pr/42",
            commit_sha=kwargs.get("commit_sha"), status_code="DELIVERY_COMPLETED",
            completed=True, idempotent=False, message="ok")


class PermissiveGitHubClient(FakeGitHubClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.push_branch_calls = []
        self.create_draft_pr_calls = []

    def push_branch(self, full_name, branch, head_sha, token=None):
        self.push_branch_calls.append((full_name, branch, head_sha))
        super().push_branch(full_name, branch, head_sha, token=token)

    def create_draft_pr(self, full_name, branch, title, body=""):
        self.create_draft_pr_calls.append((full_name, branch, title))
        return super().create_draft_pr(full_name, branch, title, body=body)


def _event_types(cp, jid):
    return {e["event_type"] for e in cp.get_events(jid)}


def _event_payloads(cp, jid, etype):
    return [json.loads(e["payload"]) for e in cp.get_events(jid)
            if e["event_type"] == etype]


class PB4CodingWorkerHandoffTests(unittest.TestCase):
    def _db(self):
        p = tempfile.mktemp(suffix=".db")
        self._dbs.append(p)
        return p

    def setUp(self):
        self._dbs = []

    def tearDown(self):
        for p in self._dbs:
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(p + ext)
                except OSError:
                    pass

    def _job(self, cp, num=1):
        cp.register(TOK)
        jid, _ = cp.create_issue_task(REPO, num, {"x": 1}, role="coding_agent")
        cp.claim(TOK)
        return jid

    def _real_release_agent(self, cp):
        delivery = DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=FakeHostGitOperations(),
            github_client=FakeGitHubRestClient(),
            token_provider=lambda r: "tok", allowed_repos={REPO})
        return ReleaseAgent(cp, delivery, REPO)

    # 1) Test passes + real local commit -> real sha, commit exists, COMMIT_CREATED
    #    + RELEASE_HANDOFF_REQUESTED.
    def test_pass_real_commit_records_commit_created_and_handoff(self):
        repo_dir, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        jid = self._job(cp, 1)
        ra = self._real_release_agent(cp)
        worker = WorkerAgent(cp, github=None,
                             agent=RealCommitAgentRunner(real_sha),
                             release_agent=ra, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())

        self.assertTrue(res.tests_passed)
        self.assertTrue(res.local_commit_created)
        self.assertEqual(res.commit_sha, real_sha)
        self.assertTrue(res.handoff_recorded)
        # The commit object genuinely exists in the repo.
        exist = subprocess.run(
            ["git", "-C", repo_dir, "cat-file", "-e", real_sha],
            capture_output=True)
        self.assertEqual(exist.returncode, 0)
        evs = _event_types(cp, jid)
        self.assertIn(EVENT_COMMIT_CREATED, evs)
        self.assertIn(EVENT_RELEASE_HANDOFF_REQUESTED, evs)
        job = cp.get_job(jid)
        self.assertEqual(job.get("commit_sha"), real_sha)
        # PB-4: the handoff request carries the SAME real commit_sha.
        ho_payloads = _event_payloads(cp, jid, EVENT_RELEASE_HANDOFF_REQUESTED)
        self.assertTrue(ho_payloads)
        self.assertEqual(ho_payloads[0]["commit_sha"], real_sha)

    # 2) Test fails -> no handoff, no push, no PR.
    def test_agent_failure_no_handoff_no_push_no_pr(self):
        cp = ControlPlane(self._db())
        jid = self._job(cp, 2)
        spy = SpyReleaseAgent()
        permissive = PermissiveGitHubClient()
        worker = WorkerAgent(cp, github=permissive,
                             agent=FailingAgentRunner(),
                             release_agent=spy, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())
        self.assertFalse(res.tests_passed)
        self.assertFalse(res.handoff_recorded)
        self.assertEqual(spy.calls, [])
        self.assertEqual(permissive.push_branch_calls, [])
        self.assertEqual(permissive.create_draft_pr_calls, [])

    # 3) No commit -> even if the agent claims done, never enter delivery.
    def test_no_commit_no_delivery(self):
        cp = ControlPlane(self._db())
        jid = self._job(cp, 3)
        spy = SpyReleaseAgent()
        worker = WorkerAgent(cp, github=None,
                             agent=NoCommitAgentRunner(),
                             release_agent=spy, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())
        self.assertFalse(res.local_commit_created)
        self.assertFalse(res.handoff_recorded)
        self.assertEqual(spy.calls, [])

    # 4) Simulated SHA -> rejected, never delivered.
    def test_simulated_sha_rejected(self):
        cp = ControlPlane(self._db())
        jid = self._job(cp, 4)
        spy = SpyReleaseAgent()
        worker = WorkerAgent(cp, github=None,
                             agent=SimulatedAgentRunner(),
                             release_agent=spy, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())
        self.assertFalse(res.local_commit_created)
        self.assertFalse(res.handoff_recorded)
        self.assertEqual(spy.calls, [])

    # 5) Coding Worker holds no delivery credentials and builds no authz.
    def test_coding_worker_has_no_delivery_credentials(self):
        _, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        jid = self._job(cp, 5)
        ra = self._real_release_agent(cp)
        permissive = PermissiveGitHubClient()
        worker = WorkerAgent(cp, github=permissive,
                             agent=RealCommitAgentRunner(real_sha),
                             release_agent=ra, repo=REPO)
        # No token broker / delivery credential on the Coding Worker.
        self.assertFalse(hasattr(worker, "broker"))
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())
        # Even holding a permissive github client, it never pushes / opens PR.
        self.assertEqual(permissive.push_branch_calls, [])
        self.assertEqual(permissive.create_draft_pr_calls, [])
        # Delivery only happened through the ReleaseAgent (controlled controller).
        self.assertTrue(res.handoff_recorded)

    # 6) Handoff uses the SAME commit_sha the Coding Worker produced.
    def test_handoff_same_commit_sha(self):
        _, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        jid = self._job(cp, 6)
        spy = SpyReleaseAgent()
        worker = WorkerAgent(cp, github=None,
                             agent=RealCommitAgentRunner(real_sha),
                             release_agent=spy, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())
        self.assertEqual(res.commit_sha, real_sha)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(spy.calls[0]["commit_sha"], real_sha)
        self.assertEqual(spy.calls[0]["expected_sha"], real_sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
