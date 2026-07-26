"""Tests for the ONE-DAY-COLLABORATION-DEMO-RUNNER (Agent F vertical slice).

Deterministic, offline, no network, no real token, no real push/PR.

Required coverage
------------------
* happy path
* CI failure stops
* duplicate run reuses PR
* acceptance before CI rejected
* completion before acceptance rejected
* second run does not create second PR
* protected repo rejected

Plus guard checks: live-smoke rejects the wrong repo, a wrong Human-Owner token
is refused, and merge is never invoked.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from run_collaboration_demo import (  # noqa: E402
    DemoRunner,
    DemoState,
    DemoRejected,
    DemoError,
    build_offline_runner,
    build_live_smoke_runner,
)

from hermes_worker.delivery import (  # noqa: E402
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
    InMemoryDeliveryRegistry,
    DUPLICATE_SUPPRESSED,
    PROTECTED_REPOSITORY,
)


SMOKE = "yzhlx/hermes-open-swe-smoke-test"
PROTECTED = "yzhlx/hermes-learning-os"
SHA = "a" * 40
TOKEN = "owner-token"


class CiHolder:
    """Controllable CI-status provider for offline tests."""

    def __init__(self, value: str = "pending"):
        self.value = value

    def __call__(self, pr_number: int) -> str:
        return self.value


def make_shared():
    return {
        "git": FakeGitWorkspaceInspector(path="/dev/null/ws"),
        "git_ops": FakeHostGitOperations(),
        "delivery_github": FakeGitHubRestClient(),
        "token_provider": (lambda repo: "x-access-token-fake"),
        "registry": InMemoryDeliveryRegistry(),
    }


def make_runner(shared, *, ci="success", repository=SMOKE, local_sha=SHA,
                expected_sha=SHA, token=TOKEN, task="T1"):
    return DemoRunner(
        mode="offline",
        repository=repository,
        task_id=task,
        worktree_path="/dev/null/ws",
        local_commit_sha=local_sha,
        expected_sha=expected_sha,
        title="t",
        body="b",
        human_owner_token=token,
        git=shared["git"],
        git_ops=shared["git_ops"],
        delivery_github=shared["delivery_github"],
        token_provider=shared["token_provider"],
        registry=shared["registry"],
        ci_provider=CiHolder(ci),
    )


class HappyPathTests(unittest.TestCase):
    def test_happy_path_reaches_task_completed(self):
        r = make_runner(make_shared(), ci="success")
        r.run()
        self.assertEqual(r.state, DemoState.FINAL_ACCEPTANCE_READY)
        self.assertEqual(r.ci_status, "success")
        self.assertIsNotNone(r.pr_number)

        r.final_accept(TOKEN)
        self.assertTrue(r.accepted)
        self.assertEqual(r.state, DemoState.ACCEPTED)

        r.complete()
        self.assertEqual(r.state, DemoState.COMPLETED)
        self.assertEqual(r.result, "TASK_COMPLETED")
        self.assertTrue(r.completed)

        # Structured summary is Buzz-readable.
        s = r.summary()
        self.assertEqual(s["schema"], "hermes.demo.one_day_collaboration.v1")
        self.assertEqual(s["state"], "COMPLETED")
        self.assertEqual(s["result"], "TASK_COMPLETED")
        self.assertEqual(s["accepted"], True)
        self.assertEqual(s["completed"], True)
        self.assertIsNotNone(s["pr_number"])

        # Delivery happened ONLY through the canonical controller; no merge.
        self.assertGreaterEqual(len(r.git_ops.push_calls), 1)
        self.assertEqual(r.delivery_github.merge_calls, [])


class CiFailureStopsTests(unittest.TestCase):
    def test_ci_failure_stops_at_ci_pending(self):
        r = make_runner(make_shared(), ci="failure")
        r.run()
        self.assertEqual(r.state, DemoState.CI_PENDING)
        self.assertEqual(r.ci_status, "failure")
        # Acceptance must be refused before CI is green.
        with self.assertRaises(DemoRejected) as ctx:
            r.final_accept(TOKEN)
        self.assertEqual(ctx.exception.code, "ACCEPTANCE_BEFORE_CI")

    def test_ci_pending_stops_at_ci_pending(self):
        r = make_runner(make_shared(), ci="pending")
        r.run()
        self.assertEqual(r.state, DemoState.CI_PENDING)
        self.assertEqual(r.ci_status, "pending")
        with self.assertRaises(DemoRejected) as ctx:
            r.final_accept(TOKEN)
        self.assertEqual(ctx.exception.code, "ACCEPTANCE_BEFORE_CI")


class DuplicateReuseTests(unittest.TestCase):
    def test_duplicate_run_reuses_pr(self):
        shared = make_shared()
        r1 = make_runner(shared, ci="success")
        r1.run()
        self.assertEqual(r1.state, DemoState.FINAL_ACCEPTANCE_READY)
        pr1 = r1.pr_number
        self.assertIsNotNone(pr1)

        # Second run against the SAME shared github + registry.
        r2 = make_runner(shared, ci="success")
        r2.run()
        self.assertEqual(r2.state, DemoState.FINAL_ACCEPTANCE_READY)
        # The Draft PR is reused (same number), not re-created.
        self.assertEqual(r2.pr_number, pr1)
        self.assertEqual(r2.delivery_status.status_code, DUPLICATE_SUPPRESSED)

    def test_second_run_does_not_create_second_pr(self):
        shared = make_shared()
        make_runner(shared, ci="success").run()
        make_runner(shared, ci="success").run()
        # Exactly one Draft PR ever exists.
        self.assertEqual(len(shared["delivery_github"].pr_numbers()), 1)


class GuardRejectionTests(unittest.TestCase):
    def test_acceptance_before_ci_rejected(self):
        for ci in ("pending", "failure"):
            r = make_runner(make_shared(), ci=ci)
            r.run()
            self.assertNotEqual(r.state, DemoState.FINAL_ACCEPTANCE_READY)
            with self.assertRaises(DemoRejected) as ctx:
                r.final_accept(TOKEN)
            self.assertEqual(ctx.exception.code, "ACCEPTANCE_BEFORE_CI")

    def test_completion_before_acceptance_rejected(self):
        # After CI green but before final_accept.
        r = make_runner(make_shared(), ci="success")
        r.run()
        self.assertEqual(r.state, DemoState.FINAL_ACCEPTANCE_READY)
        with self.assertRaises(DemoRejected) as ctx:
            r.complete()
        self.assertEqual(ctx.exception.code, "COMPLETION_BEFORE_ACCEPTANCE")
        self.assertFalse(r.completed)

        # Even from an earlier state, complete() must refuse.
        r2 = make_runner(make_shared(), ci="pending")
        r2.run()
        self.assertEqual(r2.state, DemoState.CI_PENDING)
        with self.assertRaises(DemoRejected) as ctx2:
            r2.complete()
        self.assertEqual(ctx2.exception.code, "COMPLETION_BEFORE_ACCEPTANCE")

    def test_wrong_human_owner_token_rejected(self):
        r = make_runner(make_shared(), ci="success")
        r.run()
        with self.assertRaises(DemoRejected) as ctx:
            r.final_accept("not-the-token")
        self.assertEqual(ctx.exception.code, "ACCEPTANCE_TOKEN_MISMATCH")
        self.assertFalse(r.accepted)

    def test_protected_repo_rejected(self):
        r = make_runner(make_shared(), ci="success", repository=PROTECTED)
        r.run()
        self.assertEqual(r.state, DemoState.BLOCKED)
        self.assertEqual(r.rejection, PROTECTED_REPOSITORY)
        self.assertIsNone(r.pr_number)

    def test_live_smoke_rejects_wrong_repo(self):
        with self.assertRaises(DemoError) as ctx:
            build_live_smoke_runner(
                repository=PROTECTED, task_id="T1", worktree_path="x",
                local_commit_sha=SHA, expected_sha=SHA, title="t", body="b",
                human_owner_token=TOKEN)
        self.assertEqual(ctx.exception.code, "LIVE_SMOKE_REPO_NOT_ALLOWED")

    def test_live_smoke_wiring_reaches_acceptance_with_fakes(self):
        # No real network: inject canonical-shaped fakes to prove the live
        # factory wires DeliveryController + CI client correctly.
        fake_gh = FakeGitHubRestClient()
        fake_token = lambda repo: "x-access-token-fake"  # noqa: E731
        r = build_live_smoke_runner(
            repository=SMOKE, task_id="T1", worktree_path="/dev/null/ws",
            local_commit_sha=SHA, expected_sha=SHA, title="t", body="b",
            human_owner_token=TOKEN,
            git=FakeGitWorkspaceInspector(path="/dev/null/ws"),
            git_ops=FakeHostGitOperations(),
            delivery_github=fake_gh, ci_client=fake_gh,
            token_provider=fake_token)
        self.assertEqual(r.mode, "live-smoke")
        r.run()
        self.assertEqual(r.state, DemoState.CI_PENDING)  # CI seeded pending
        fake_gh.set_ci_status(r.pr_number, "success")
        r.run()
        self.assertEqual(r.state, DemoState.FINAL_ACCEPTANCE_READY)
        self.assertEqual(fake_gh.merge_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
