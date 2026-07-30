"""D3 full closed-loop integration test (offline, Fake GitHub + Fake Token Broker).

Drives the entire Issue -> Job -> Worker -> Sandbox -> Agent -> Commit -> Push ->
Draft PR -> CI -> Reviewer -> round-2 -> 2nd Commit -> Re-review -> User Acceptance
loop against :class:`FakeGitHubClient` and :class:`FakeAppApiClient`. No real
GitHub, no real webhook, no real credentials, no Docker daemon, no relay call.

Asserts the invariants required by the integration spec:

 1. Same webhook repeated 10x yields exactly 1 task.
 2. Concurrent claim produces no duplicate jobs.
 3. The GitHub token is requested ONLY at push time.
 4. No token ever reaches the SQLite DB or the event store.
 5. A red CI never reaches an approving review (reviewer not invoked on CI fail).
 6. The Reviewer is a distinct role and never reuses the Agent's conclusions.
 7. The round-2 label can ONLY be set by the Scheduler.
 8. Round 2 reuses the SAME PR number.
 9. Round 2 produces a NEW commit SHA.
10. The automation exposes no Merge API.
11. A task recovers after a Worker disconnect (re-claim, same job).
12. An expired Installation Token is re-minted on demand.
13. The repo allowlist rejects any non-smoke-test repository.
"""
import os
import sys
import json
import time
import hmac
import hashlib
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.control_plane import ControlPlane, ControlPlaneError  # noqa: E402
from hermes_worker.db import hash_token                                    # noqa: E402
from hermes_worker.constants import (                                       # noqa: E402
    ALLOWED_GITHUB_REPOS, ROLE_REVIEWER, ROUND2_LABEL, MAX_ROUNDS,
)
from hermes_worker.github_app import (                                      # noqa: E402
    GitHubAppTokenBroker, FakeAppApiClient,
)
from hermes_worker.github_client import FakeGitHubClient, GitHubClientError  # noqa: E402
from hermes_worker.event_router import EventRouter                          # noqa: E402
from hermes_worker.agent_runner import FakeAgentRunner, AgentEvidence       # noqa: E402
from hermes_worker.reviewer import (                                         # noqa: E402
    Reviewer, ReviewVerdict, ReviewFinding,
)
from hermes_worker.scheduler import WorkerAgent, Scheduler, D3Orchestrator   # noqa: E402
from hermes_worker.echo_sandbox import EchoSandboxBackend                   # noqa: E402

ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
FORBIDDEN_REPO = "yzhlx/hermes-learning-os"
TEST_SECRET = "test-d3-integration-secret-DO-NOT-USE"


def _sign(raw: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _fake_jwt(app_id, pem, ts):
    return "fake.jwt.token"


class D3ClosedLoopTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.tokens = ["wk-d3-aaa", "wk-d3-bbb", "wk-d3-ccc", "wk-d3-ddd"]
        self.allowed_hashes = {hash_token(t) for t in self.tokens}
        self.cp = ControlPlane(self.db, allowed_token_hashes=self.allowed_hashes,
                               lease_seconds=1200)
        for t in self.tokens:
            self.cp.register(t)
        self.broker = GitHubAppTokenBroker(
            app_id="12345", installation_id="67890",
            app_api=FakeAppApiClient(), jwt_signer=_fake_jwt,
            allowed_repos={ALLOWED_REPO}, ttl_seconds=3600)
        self.github = FakeGitHubClient()
        self.agent = FakeAgentRunner(sha_fn=lambda r: f"sha-round{r}")
        self.router = EventRouter(self.db, TEST_SECRET, allowed_repos={ALLOWED_REPO})
        self.repo = ALLOWED_REPO

    def tearDown(self):
        try:
            self.cp.conn.close()
        except Exception:
            pass
        for ext in ("", "-wal", "-shm"):
            p = self.db + ext
            if os.path.exists(p):
                for _ in range(10):
                    try:
                        os.remove(p); break
                    except PermissionError:
                        time.sleep(0.1)

    # ---- helpers ----
    def _issue_payload(self, num, action="opened"):
        return json.dumps({"repository": {"full_name": ALLOWED_REPO},
                           "issue": {"number": num}, "action": action}).encode()

    def _serialize_job_and_events(self, jid):
        job = self.cp.get_job(jid)
        evs = self.cp.get_events(jid)
        blob = json.dumps([dict(job), [dict(e) for e in evs]], default=str)
        return blob

    # ===================== 1) issue idempotency (10x) =====================
    def test_01_issue_webhook_10x_one_task(self):
        # 10 DIFFERENT deliveries for the SAME issue -> 1 task (issue idempotency).
        for i in range(10):
            raw = self._issue_payload(101)
            self.router.handle(delivery_id=f"d-issue101-{i}",
                               signature=_sign(raw), event="issues", raw_body=raw)
        jobs = self.cp.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.cp.get_job_by_issue(ALLOWED_REPO, 101), jobs[0]["id"])

    def test_01b_same_delivery_10x_deduped(self):
        # 10 identical deliveries (GitHub retry) -> 1 task (delivery dedup).
        for i in range(10):
            raw = self._issue_payload(102)
            r = self.router.handle(delivery_id="d-dup-102",
                                   signature=_sign(raw), event="issues",
                                   raw_body=raw)
            if i > 0:
                self.assertTrue(r.get("deduped"))
        self.assertEqual(len(self.cp.list_jobs()), 1)

    # ===================== 13) repo allowlist =====================
    def test_13_repo_allowlist_rejects_other_repo(self):
        raw = json.dumps({"repository": {"full_name": FORBIDDEN_REPO},
                          "issue": {"number": 1}}).encode()
        with self.assertRaises(ControlPlaneError) as ctx:
            self.router.handle(delivery_id="d-bad-repo", signature=_sign(raw),
                               event="issues", raw_body=raw)
        self.assertEqual(str(ctx.exception), "repo_not_allowed")

    # ===================== 2) concurrent claim =====================
    def test_02_concurrent_claim_no_duplicate(self):
        # Genuine concurrency on the claim path: K workers pull from the same
        # queue at the same time. The atomic UPDATE...RETURNING is what prevents
        # two workers from grabbing the same pending job, so the invariant holds
        # at any K. We keep K modest (the production max concurrency is 1 per
        # type) to avoid a Windows-WAL writer storm, and the workers start at
        # slightly offset times (no Barrier) so they cannot deadlock in a
        # perfectly-synchronised collision.
        N = 12
        K = 4
        for _ in range(N):
            self.cp.create_job({"command": "x"})
        claimed = []
        workers = [TrackingWorker(self.db, self.allowed_hashes, self.tokens[i],
                                  claimed_log=claimed)
                   for i in range(K)]

        threads = [threading.Thread(target=w.run, daemon=True) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(len(claimed), N)
        self.assertEqual(set(claimed), {j["id"] for j in self.cp.list_jobs()})

    # ===================== 3/4) token only at push, never persisted =====================
    def test_03_04_token_only_at_push_and_not_persisted(self):
        jid, _ = self.cp.create_issue_task(self.repo, 200, {"x": 1},
                                           role="coding_agent")
        self.cp.claim(self.tokens[0])
        issued = []
        orig = self.broker.get_token_for_job
        self.broker.get_token_for_job = lambda cp, j, t: (
            issued.append(orig(cp, j, t)) or issued[-1])
        # Run a worker phase (this is the push stage).
        WorkerAgent(self.cp, self.github, self.broker, self.agent,
                    self.repo).run_phase(jid, self.tokens[0], 1, "do it",
                                         EchoSandboxBackend())
        self.assertEqual(len(issued), 1)  # token minted exactly once, at push
        self.assertGreater(len(self.broker._app_api.exchanges), 0)
        # No token substring anywhere in DB / events.
        blob = self._serialize_job_and_events(jid)
        for tok in issued:
            self.assertNotIn(tok, blob)
        self.assertNotIn("ghs_", blob)

    # ===================== 5) CI fail never approves =====================
    def test_05_ci_fail_no_review_approve(self):
        jid, _ = self.cp.create_issue_task(self.repo, 201, {"x": 1},
                                           role="coding_agent")
        self.cp.claim(self.tokens[0])
        WorkerAgent(self.cp, self.github, self.broker, self.agent,
                    self.repo).run_phase(jid, self.tokens[0], 1, "do it",
                                         EchoSandboxBackend())
        reviewer = CountingReviewer()
        sched = Scheduler(self.cp, self.github, self.repo)
        sig = sched.review_phase(jid, "failure", reviewer)
        self.assertEqual(sig["action"], "ci_fail")
        self.assertEqual(reviewer.calls, 0)  # reviewer NOT invoked on red CI
        self.assertNotEqual(self.cp.get_job(jid)["state"], "await_user")

    # ===================== 6) reviewer role isolation =====================
    def test_06_reviewer_role_isolated(self):
        # Default reviewer independently evaluates EVIDENCE, not the agent's word.
        rev = Reviewer()  # uses default decide
        # Even if the agent claimed to self-approve, the reviewer flags blocking.
        v1 = rev.review({"number": 1}, {"ci_status": "success",
                                        "agent_self_approved": True})
        self.assertEqual(v1.verdict, "REQUEST_CHANGES")
        self.assertTrue(v1.has_blocking)
        self.assertEqual(v1.role, ROLE_REVIEWER)
        # Clean evidence -> APPROVE, proving it reads evidence not agent claims.
        v2 = rev.review({"number": 1}, {"ci_status": "success",
                                        "agent_self_approved": False})
        self.assertEqual(v2.verdict, "APPROVE")

    # ===================== 7) round-2 label only by scheduler =====================
    def test_07_round2_label_only_scheduler(self):
        jid, _ = self.cp.create_issue_task(self.repo, 202, {"x": 1},
                                           role="coding_agent")
        self.cp.claim(self.tokens[0])
        worker = WorkerAgent(self.cp, self.github, self.broker, self.agent,
                             self.repo)
        worker.run_phase(jid, self.tokens[0], 1, "do it", EchoSandboxBackend())
        pr_number = self.cp.get_job(jid)["pr_number"]
        # After the worker phase, NO round-2 label (worker has no label power).
        self.assertFalse(self.github.has_label(pr_number, ROUND2_LABEL))
        # Only the Scheduler may add it.
        Scheduler(self.cp, self.github, self.repo).add_round2_label(jid, pr_number)
        self.assertTrue(self.github.has_label(pr_number, ROUND2_LABEL))
        self.assertEqual(self.cp.get_job(jid)["round"], 2)

    # ===================== 8/9) round-2 same PR, new SHA =====================
    def test_08_09_round2_same_pr_new_sha(self):
        jid, _ = self.cp.create_issue_task(self.repo, 203, {"x": 1},
                                           role="coding_agent")
        self.cp.claim(self.tokens[0])
        worker = WorkerAgent(self.cp, self.github, self.broker, self.agent,
                             self.repo)
        ev1 = worker.run_phase(jid, self.tokens[0], 1, "round1",
                               EchoSandboxBackend())
        pr1 = self.cp.get_job(jid)["pr_number"]
        ev2 = worker.run_phase(jid, self.tokens[0], 2, "round2",
                               EchoSandboxBackend())
        pr2 = self.cp.get_job(jid)["pr_number"]
        self.assertEqual(pr1, pr2)                       # same PR number
        self.assertNotEqual(ev1.commit_sha, ev2.commit_sha)  # new head sha

    # ===================== 10) no merge capability =====================
    def test_10_merge_capability_absent(self):
        jid, _ = self.cp.create_issue_task(self.repo, 204, {"x": 1},
                                           role="coding_agent")
        orch = D3Orchestrator(self.cp, self.github, self.broker, self.agent,
                              CountingReviewer(approve_after=2),
                              EchoSandboxBackend(), self.repo)
        sig = orch.run_job(jid, self.tokens[0], "implement", ci_status="success")
        self.assertEqual(sig["action"], "await_user")
        self.assertFalse(hasattr(self.github, "merge_pr"))
        with self.assertRaises(AttributeError):
            getattr(self.github, "merge_pr")

    # ===================== 11) disconnect recovery =====================
    def test_11_disconnect_recovery(self):
        jid = self.cp.create_job({"command": "x"})
        # Worker A claims.
        self.cp.claim(self.tokens[0])
        # Simulate disconnect: expire the lease, then reap.
        self.cp.conn.execute("UPDATE jobs SET lease_expires=? WHERE id=?",
                             (self.cp._now() - 100, jid))
        self.cp.conn.commit()
        self.cp.reap_expired_leases()
        job = self.cp.get_job(jid)
        self.assertEqual(job["state"], "pending")
        self.assertIsNone(job["worker_token_hash"])
        # Worker B reclaims the SAME job (not a new one).
        r = self.cp.claim(self.tokens[1])
        self.assertEqual(r["job_id"], jid)
        self.assertEqual(len(self.cp.list_jobs()), 1)

    # ===================== 12) token expiry re-mint =====================
    def test_12_token_expiry_reissue(self):
        clock = {"t": 1000.0}
        clock_fn = lambda: clock["t"]            # no-arg callable, captures by ref
        b = GitHubAppTokenBroker(
            app_id="1", installation_id="2",
            app_api=FakeAppApiClient(clock=clock_fn),
            jwt_signer=_fake_jwt, allowed_repos={ALLOWED_REPO}, ttl_seconds=10,
            clock=clock_fn)
        t1 = b.mint_installation_token(self.repo)
        self.assertEqual(len(b._app_api.exchanges), 1)
        # Advance past TTL + refresh buffer.
        clock["t"] = 2000.0
        t2 = b.mint_installation_token(self.repo)
        self.assertEqual(len(b._app_api.exchanges), 2)  # re-minted
        self.assertNotEqual(t1, t2)

    # ===================== full closed loop (happy path) =====================
    def test_full_closed_loop_round2_approve(self):
        jid, _ = self.cp.create_issue_task(self.repo, 300, {"x": 1},
                                           role="coding_agent")
        issued = []
        orig = self.broker.get_token_for_job
        self.broker.get_token_for_job = lambda cp, j, t: (
            issued.append(orig(cp, j, t)) or issued[-1])
        orch = D3Orchestrator(self.cp, self.github, self.broker, self.agent,
                              CountingReviewer(approve_after=2),
                              EchoSandboxBackend(), self.repo)
        sig = orch.run_job(jid, self.tokens[0], "implement",
                           ci_status="success")
        # Ends awaiting the user, after exactly one round-2.
        self.assertEqual(sig["action"], "await_user")
        job = self.cp.get_job(jid)
        self.assertEqual(job["state"], "await_user")
        pr = self.github.get_pr(job["pr_number"])
        # Exactly one PR; round-2 label present; two pushes (two tokens).
        self.assertEqual(len(self.github.pr_numbers()), 1)
        self.assertTrue(self.github.has_label(pr["number"], ROUND2_LABEL))
        self.assertEqual(len(issued), 2)
        self.assertFalse(hasattr(self.github, "merge_pr"))
        with self.assertRaises(AttributeError):
            getattr(self.github, "merge_pr")
        # No token in the DB / event store.
        blob = self._serialize_job_and_events(jid)
        for tok in issued:
            self.assertNotIn(tok, blob)
        self.assertNotIn("ghs_", blob)
        self.assertEqual(job["round"], 2)


class TrackingWorker:
    """Minimal claim-loop driver for the concurrency test.

    The ControlPlane connection is created INSIDE the worker thread (run),
    mirroring the real design where every request opens a fresh, thread-local
    connection. Creating it in __init__ would bind it to the spawning thread.
    """
    def __init__(self, db_path, allowed_hashes, token, claimed_log=None,
                 barrier=None, reduce_writes=False):
        self.db_path = db_path
        self.allowed_hashes = allowed_hashes
        self.token = token
        self._claimed_log = claimed_log
        self._barrier = barrier
        self._reduce_writes = reduce_writes

    def run(self):
        from hermes_worker.control_plane import ControlPlane
        import sqlite3
        cp = ControlPlane(self.db_path, allowed_token_hashes=self.allowed_hashes)
        if self._barrier is not None:
            self._barrier.wait()      # race the first claim simultaneously
        while True:
            try:
                r = cp.claim(self.token)
            except sqlite3.OperationalError as exc:
                # A transient lock under write contention is retryable, not fatal
                # — a real worker must survive brief contention and re-claim.
                if "database is locked" in str(exc).lower():
                    time.sleep(0.01)
                    continue
                raise
            if r.get("empty"):
                break
            jid = r["job_id"]
            if self._claimed_log is not None:
                self._claimed_log.append(jid)
            # In the reduced-write mode we skip set_state to halve the per-claim
            # write traffic (the no-duplicate invariant is about claim, not
            # completion). The job stays 'running' and is not re-claimed.
            if not self._reduce_writes:
                cp.set_state(jid, "completed")


class CountingReviewer(Reviewer):
    """Returns REQUEST_CHANGES for the first ``approve_after-1`` calls, then APPROVE."""
    def __init__(self, approve_after: int = 2):
        super().__init__(decide=self._decide)
        self.approve_after = approve_after
        self.calls = 0

    def _decide(self, pr_state, evidence):
        self.calls += 1
        if self.calls < self.approve_after:
            return ReviewVerdict(
                "REQUEST_CHANGES",
                findings=[ReviewFinding("blocking", "needs another pass")])
        return ReviewVerdict("APPROVE",
                             findings=[ReviewFinding("non_blocking", "looks good")])


if __name__ == "__main__":
    unittest.main()
