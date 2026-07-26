"""PB-1 — Release Agent is the ONLY production caller of DeliveryController.deliver().

Verifies the single controlled delivery path:
  Coding Worker -> commit_sha -> ReleaseAgent.deliver_task(...)
      -> DeliveryController.deliver() -> push + Draft PR

And that no other production component (notably the Coding Worker /
``WorkerAgent``) may push or open Draft PRs directly.
"""
import os
import re
import sys
import json
import subprocess
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hermes_worker.control_plane import ControlPlane
from hermes_worker.delivery import (
    DeliveryController,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
    COMMIT_SHA_MISMATCH,
)
from hermes_worker.release_delivery_coordinator import ReleaseAgent
from hermes_worker.scheduler import WorkerAgent
from hermes_worker.agent_runner import AgentRunner, AgentEvidence
from hermes_worker.echo_sandbox import EchoSandboxBackend
from hermes_worker.github_client import FakeGitHubClient

REPO = "yzhlx/hermes-open-swe-smoke-test"
TOK = "wk-pb1-test"


def _make_real_commit():
    """Create a real git commit in a temp repo; return (repo_dir, sha)."""
    d = tempfile.mkdtemp(prefix="pb1-commit-")
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


class PermissiveGitHubClient(FakeGitHubClient):
    """A github client that WOULD push / open PRs if asked, and records it."""

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


def _grep_files(pattern):
    out = subprocess.run(
        ["git", "-C", _ROOT, "grep", "--untracked", "-nE", pattern,
         "--", "hermes_worker"],
        capture_output=True, text=True)
    files = set()
    for line in out.stdout.splitlines():
        if ":" in line:
            files.add(line.split(":", 1)[0])
    return files


class PB1ReleaseAgentDeliveryTests(unittest.TestCase):
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

    # 1) Valid commit -> controlled push + Draft PR + canonical events.
    def test_valid_deliver_via_worker_handoff(self):
        _, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        cp.register(TOK)
        jid, _ = cp.create_issue_task(REPO, 1, {"x": 1}, role="coding_agent")
        cp.claim(TOK)
        delivery = DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=FakeHostGitOperations(),
            github_client=FakeGitHubRestClient(),
            token_provider=lambda r: "tok", allowed_repos={REPO})
        ra = ReleaseAgent(cp, delivery, REPO)
        worker = WorkerAgent(cp, github=None,
                              agent=RealCommitAgentRunner(real_sha),
                              release_agent=ra, repo=REPO)
        res = worker.run_phase(jid, TOK, 1, "implement", EchoSandboxBackend())

        self.assertTrue(res.handoff_recorded)
        self.assertEqual(res.commit_sha, real_sha)
        self.assertIsNotNone(res.pr_number)
        # Push + Draft PR actually happened through the controlled controller.
        self.assertEqual(len(delivery.git_ops.push_calls), 1)
        self.assertEqual(len(delivery.github.pr_calls), 1)
        # Canonical delivery events recorded on the event store.
        evs = _event_types(cp, jid)
        self.assertIn("push_completed", evs)
        self.assertIn("draft_pr_created", evs)
        self.assertIn("ci_pending", evs)
        job = cp.get_job(jid)
        self.assertEqual(job["pr_number"], res.pr_number)
        self.assertEqual(job.get("commit_sha"), real_sha)
        # PB-1: the Release Agent records delivered_commit_sha on the
        # canonical delivery event (control_plane's job row does not persist
        # that column, so verify via the event store).
        ci_payloads = _event_payloads(cp, jid, "ci_pending")
        self.assertTrue(ci_payloads)
        self.assertEqual(ci_payloads[0]["delivered_commit_sha"], real_sha)

    # 2) commit_sha != expected_sha -> fail, no push, no PR, no fake success.
    def test_sha_mismatch_blocked(self):
        cp = ControlPlane(self._db())
        delivery = DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=FakeHostGitOperations(),
            github_client=FakeGitHubRestClient(),
            token_provider=lambda r: "tok", allowed_repos={REPO})
        ra = ReleaseAgent(cp, delivery, REPO)
        jid = cp.create_job({"x": 1})
        st = ra.deliver_task(
            job_id=jid, task_id="T1", repository=REPO,
            commit_sha="a" * 40, expected_sha="b" * 40,
            title="t", body="b", test_summary="", security_summary="")
        self.assertEqual(st.status_code, COMMIT_SHA_MISMATCH)
        self.assertFalse(st.completed)
        self.assertEqual(len(delivery.git_ops.push_calls), 0)
        self.assertEqual(len(delivery.github.pr_calls), 0)
        evs = _event_types(cp, jid)
        self.assertIn("delivery_blocked", evs)
        self.assertNotIn("push_completed", evs)
        self.assertNotIn("draft_pr_created", evs)

    # 3) Repeated deliver -> same Draft PR, no second push, no second PR.
    def test_idempotent_repeat_same_pr(self):
        _, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        cp.register(TOK)
        jid, _ = cp.create_issue_task(REPO, 2, {"x": 1}, role="coding_agent")
        cp.claim(TOK)
        delivery = DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=FakeHostGitOperations(),
            github_client=FakeGitHubRestClient(),
            token_provider=lambda r: "tok", allowed_repos={REPO})
        ra = ReleaseAgent(cp, delivery, REPO)
        worker = WorkerAgent(cp, github=None,
                              agent=RealCommitAgentRunner(real_sha),
                              release_agent=ra, repo=REPO)
        r1 = worker.run_phase(jid, TOK, 1, "impl", EchoSandboxBackend())
        r2 = worker.run_phase(jid, TOK, 2, "impl2", EchoSandboxBackend())
        self.assertEqual(r1.pr_number, r2.pr_number)
        self.assertEqual(len(delivery.git_ops.push_calls), 1)
        self.assertEqual(len(delivery.github.pr_calls), 1)

    # 4) WorkerAgent bypass: even with a permissive github client it must NOT
    #    call push_branch / create_draft_pr, and must hold no delivery creds.
    def test_workeragent_bypass_no_direct_push_or_pr(self):
        _, real_sha = _make_real_commit()
        cp = ControlPlane(self._db())
        cp.register(TOK)
        jid, _ = cp.create_issue_task(REPO, 3, {"x": 1}, role="coding_agent")
        cp.claim(TOK)
        delivery = DeliveryController(
            git=FakeGitWorkspaceInspector(),
            git_operations=FakeHostGitOperations(),
            github_client=FakeGitHubRestClient(),
            token_provider=lambda r: "tok", allowed_repos={REPO})
        ra = ReleaseAgent(cp, delivery, REPO)
        permissive = PermissiveGitHubClient()
        worker = WorkerAgent(cp, github=permissive,
                             agent=RealCommitAgentRunner(real_sha),
                             release_agent=ra, repo=REPO)
        # No token broker / delivery credential on the Coding Worker.
        self.assertFalse(hasattr(worker, "broker"))
        res = worker.run_phase(jid, TOK, 1, "impl", EchoSandboxBackend())
        self.assertEqual(permissive.push_branch_calls, [])
        self.assertEqual(permissive.create_draft_pr_calls, [])
        # But the handoff still delivered via the ReleaseAgent.
        self.assertTrue(res.handoff_recorded)
        self.assertIsNotNone(res.pr_number)

    # 5) Structural scan: production push_branch / create_draft_pr call sites
    #    live ONLY in delivery.py.
    def test_structural_scan_push_pr_only_in_delivery(self):
        files = _grep_files(r"\.(push_branch|create_draft_pr)\s*\(")
        self.assertEqual(files, {"hermes_worker/delivery.py"})

    # 6) Structural scan: the only production CALLER of DeliveryController.deliver()
    #    is the ReleaseAgent coordinator (self.delivery.deliver(...)). Docstring /
    #    comment mentions of ``.deliver()`` (e.g. in scheduler.py / constants.py)
    #    are not call sites and must not be counted.
    def test_structural_scan_deliver_caller_only_release_agent(self):
        files = _grep_files(r"\.delivery\.deliver\s*\(")
        self.assertEqual(files, {"hermes_worker/release_delivery_coordinator.py"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
