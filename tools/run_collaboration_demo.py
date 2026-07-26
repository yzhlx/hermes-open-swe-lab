"""ONE-DAY-COLLABORATION-DEMO-RUNNER — end-to-end single-task Demo orchestrator.

This is **Agent F**'s vertical-slice deliverable for the one-day collaboration
demo. It is a *new, self-contained* file: it orchestrates the *existing,
approved* canonical components and never modifies them.

What it does
------------
Given a single task it runs a repeatable, auditable pipeline:

    1. verify the real local Commit exists (read-only git inspector)
    2. hand the Commit to the Release Agent
    3. the Release Agent delivers *only* through ``DeliveryController``
    4. obtain or reuse a Draft PR
    5. read CI status for that PR
    6. if CI is not green -> STOP at CI_PENDING (never pretend success)
    7. once CI is green -> produce FINAL_ACCEPTANCE
    8. wait for the Human Owner to call ``final_accept(token)``
    9. call ``complete()``
   10. emit TASK_COMPLETED
   11. emit a structured status summary that another agent ("Buzz") can read

Two modes
---------
* ``offline``       — all GitHub / git are Fake or Mock; deterministic, no network.
* ``live-smoke``    — ONLY ``yzhlx/hermes-open-swe-smoke-test`` is permitted; uses
                      the real GitHub Draft PR path; **merge is never allowed**.

Hard boundaries (enforced, never relaxed)
-----------------------------------------
* No direct ``gh push`` / ``gh pr create`` — every write goes through
  ``DeliveryController`` (canonical ``HostGitOperations`` + ``GitHubRestClient``).
* The Commit is never delivered except via the ``ReleaseAgent`` role.
* ``yzhlx/hermes-learning-os`` is protected and is always rejected.
* Merge is structurally impossible (no merge method is ever called).
* A failure is never re-labelled as success (fail-closed state machine).

This module contains no production delivery / git / GitHub logic of its own;
it *reuses* ``hermes_worker.delivery.DeliveryController`` and the canonical
clients. The ``ReleaseAgent`` here is the demo-scoped coordinator role.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
import typing as _t

# Make the repo root importable so this tool also runs as a standalone script
# (e.g. `python tools/run_collaboration_demo.py`) from any cwd.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hermes_worker.delivery import (
    DeliveryController,
    DeliveryAuthorization,
    DeliveryState,
    FakeGitHubRestClient,
    FakeGitWorkspaceInspector,
    FakeHostGitOperations,
    InMemoryDeliveryRegistry,
    LocalGitWorkspaceInspector,
)
from hermes_worker.constants import ALLOWED_GITHUB_REPOS
from hermes_worker.github_client import GitHubRestClient, RealGitHubClient
from hermes_worker.repository import HostGitOperations


# --------------------------------------------------------------------------
# Public constants
# --------------------------------------------------------------------------
DEMO_NAME = "ONE-DAY-COLLABORATION-DEMO-RUNNER"
# Agent-completion marker (this task is "ready" once this module + tests land).
DEMO_RUNNER_READY = "ONE_DAY_COLLABORATION_DEMO_RUNNER_READY_LOCAL"

TASK_COMPLETED = "TASK_COMPLETED"

# The only repo a live-smoke run may touch.
SMOKE_REPO = "yzhlx/hermes-open-swe-smoke-test"
# The protected repo that must never be accessed / written.
PROTECTED_REPO = "yzhlx/hermes-learning-os"

# Structured summary schema consumed by the "Buzz" reader agent.
SUMMARY_SCHEMA = "hermes.demo.one_day_collaboration.v1"

# CI statuses considered "green".
CI_GREEN = ("success",)


class DemoState:
    """Demo orchestrator state machine (fail-closed; never jumps to COMPLETED)."""

    INIT = "INIT"
    COMMIT_VERIFIED = "COMMIT_VERIFIED"
    DELIVERED = "DELIVERED"
    BLOCKED = "BLOCKED"
    CI_PENDING = "CI_PENDING"
    FINAL_ACCEPTANCE_READY = "FINAL_ACCEPTANCE_READY"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class DemoError(Exception):
    """Base error for the demo runner; carries a machine-readable ``code``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[{self.code}] {self.message}"


class DemoRejected(DemoError):
    """A guarded transition was refused (e.g. acceptance before CI green)."""


# --------------------------------------------------------------------------
# Release Agent — the only role permitted to call DeliveryController.deliver()
# --------------------------------------------------------------------------
class ReleaseAgent:
    """Demo-scoped coordinator role.

    The Demo orchestrator may NOT call ``DeliveryController.deliver`` directly;
    it must go through this role, which builds the explicit, task+repo+commit
    scoped ``DeliveryAuthorization`` and delegates to the canonical controller.
    """

    ROLE = "release_agent"

    def __init__(self, controller: DeliveryController):
        self.controller = controller

    def deliver_task(
        self,
        *,
        task_id: str,
        repository: str,
        local_commit_sha: str,
        expected_sha: str,
        title: str,
        body: str,
        test_summary: str = "demo automated",
        security_summary: str = "demo automated",
    ):
        authorization = DeliveryAuthorization(
            task_id=task_id,
            repository=repository,
            commit_sha=local_commit_sha,
            grant=DeliveryAuthorization.REQUIRED_GRANT,
        )
        return self.controller.deliver(
            task_id=task_id,
            repository=repository,
            local_commit_sha=local_commit_sha,
            expected_sha=expected_sha,
            authorization=authorization,
            title=title,
            body=body,
            test_summary=test_summary,
            security_summary=security_summary,
        )


# --------------------------------------------------------------------------
# Demo Runner
# --------------------------------------------------------------------------
class DemoRunner:
    """Repeatable single-task collaboration demo orchestrator.

    Dependencies (git inspector, git operations, GitHub client, token provider,
    registry, CI client) are injected so the runner is fully deterministic in
    ``offline`` mode and re-runnable (shared registry + GitHub client across
    runs => the Draft PR is reused, never re-created).
    """

    def __init__(
        self,
        *,
        mode: str,
        repository: str,
        task_id: str,
        worktree_path: str,
        local_commit_sha: str,
        expected_sha: str,
        title: str,
        body: str,
        human_owner_token: str,
        # Injected dependencies (tests pass fakes; production passes reals).
        git=None,
        git_ops=None,
        delivery_github=None,
        ci_client=None,
        token_provider=None,
        registry=None,
        ci_provider: _t.Optional[_t.Callable[[int], str]] = None,
        verbose: bool = False,
        summary_path: _t.Optional[str] = None,
    ):
        if mode not in ("offline", "live-smoke"):
            raise DemoError("BAD_MODE", f"mode must be offline|live-smoke, got {mode!r}")
        # live-smoke is only ever allowed against the smoke-test repo.
        if mode == "live-smoke" and repository != SMOKE_REPO:
            raise DemoError(
                "LIVE_SMOKE_REPO_NOT_ALLOWED",
                f"live-smoke only permits {SMOKE_REPO}, got {repository!r}",
            )

        self.mode = mode
        self.repository = repository
        self.task_id = task_id
        self.worktree_path = worktree_path
        self.local_commit_sha = local_commit_sha
        self.expected_sha = expected_sha
        self.title = title
        self.body = body
        self.human_owner_token = human_owner_token
        self.verbose = verbose
        self.summary_path = summary_path

        # --- resolve dependencies (offline defaults to fakes) ---------------
        if git is None or git_ops is None or delivery_github is None \
                or token_provider is None or registry is None:
            if mode == "offline":
                git = git or FakeGitWorkspaceInspector(path=worktree_path or "/dev/null/fake-ws")
                git_ops = git_ops or FakeHostGitOperations()
                delivery_github = delivery_github or FakeGitHubRestClient()
                token_provider = token_provider or (lambda repo: "x-access-token-fake")
                registry = registry or InMemoryDeliveryRegistry()
            else:  # live-smoke -> real canonical components
                git = git or LocalGitWorkspaceInspector(worktree_path)
                git_ops = git_ops or HostGitOperations()
                delivery_github = delivery_github or GitHubRestClient()
                token_provider = token_provider or _default_broker_provider()
                registry = registry or InMemoryDeliveryRegistry()

        self.git = git
        self.git_ops = git_ops
        self.delivery_github = delivery_github
        self.ci_client = ci_client or delivery_github
        self.token_provider = token_provider
        self.registry = registry
        self.ci_provider = ci_provider

        # Canonical delivery controller (the only writer of Draft PRs).
        self.controller = DeliveryController(
            git=self.git,
            git_operations=self.git_ops,
            github_client=self.delivery_github,
            token_provider=self.token_provider,
            registry=self.registry,
            allowed_repos=set(ALLOWED_GITHUB_REPOS),
        )
        # The Demo orchestrator only ever delivers through this role.
        self.release_agent = ReleaseAgent(self.controller)

        # --- mutable run state -------------------------------------------------
        self.state = DemoState.INIT
        self.delivery_status = None
        self.pr_number = None
        self.pr_url = None
        self.ci_status = None
        self.accepted = False
        self.completed = False
        self.result = None
        self.rejection = None
        self.message = ""
        self._commit_fail_reason = None

    # -- internal helpers ----------------------------------------------------
    def _set_state(self, state: str, **fields) -> "DemoRunner":
        self.state = state
        for k, v in fields.items():
            setattr(self, k, v)
        return self

    def _block(self, code: str, message: str) -> "DemoRunner":
        self.rejection = code
        return self._set_state(DemoState.BLOCKED, message=message)

    def _verify_commit(self) -> bool:
        try:
            if not self.git.exists():
                self._commit_fail_reason = "WORKSPACE_MISSING"
                return False
            if not self.git.is_git_repo():
                self._commit_fail_reason = "NOT_A_GIT_REPO"
                return False
            if not self.git.commit_in_history(self.local_commit_sha):
                self._commit_fail_reason = "COMMIT_NOT_IN_HISTORY"
                return False
            if self.git.is_empty_commit(self.local_commit_sha):
                self._commit_fail_reason = "COMMIT_EMPTY"
                return False
            return True
        except Exception:
            self._commit_fail_reason = "COMMIT_VERIFY_ERROR"
            return False

    def _get_ci(self, pr_number: int) -> str:
        if self.ci_provider is not None:
            return self.ci_provider(pr_number)
        return self.ci_client.get_ci_status(pr_number)

    # -- orchestration steps -------------------------------------------------
    def run(self) -> "DemoRunner":
        """Steps 1-7: verify commit, deliver via ReleaseAgent, read CI."""
        # Step 1: verify the real local Commit exists.
        if not self._verify_commit():
            return self._block(
                self._commit_fail_reason or "COMMIT_NOT_VERIFIED",
                f"local commit verification failed: {self._commit_fail_reason}",
            )
        self._set_state(DemoState.COMMIT_VERIFIED, message="local commit verified")

        # Steps 2-4: hand the Commit to the ReleaseAgent -> DeliveryController.
        dstatus = self.release_agent.deliver_task(
            task_id=self.task_id,
            repository=self.repository,
            local_commit_sha=self.local_commit_sha,
            expected_sha=self.expected_sha,
            title=self.title,
            body=self.body,
        )
        self.delivery_status = dstatus
        # Fail-closed: a blocked delivery never masquerades as success.
        if not dstatus.completed:
            return self._block(
                dstatus.status_code,
                f"delivery blocked: {dstatus.status_code}: {dstatus.message}",
            )

        # Step 5: Draft PR obtained or reused.
        self.pr_number = dstatus.pr_number
        self.pr_url = dstatus.pr_url
        self._set_state(
            DemoState.DELIVERED,
            pr_number=self.pr_number,
            pr_url=self.pr_url,
            message=f"draft PR {self.pr_number} obtained/reused",
        )

        # Step 6: CI status. If not green -> STOP at CI_PENDING.
        ci = self._get_ci(self.pr_number)
        self.ci_status = ci
        if ci not in CI_GREEN:
            return self._set_state(
                DemoState.CI_PENDING,
                ci_status=ci,
                message=f"CI not green ({ci}); stopped at CI_PENDING",
            )

        # Step 7: CI green -> produce FINAL_ACCEPTANCE.
        return self._set_state(
            DemoState.FINAL_ACCEPTANCE_READY,
            ci_status=ci,
            message="CI green; FINAL_ACCEPTANCE produced; awaiting Human Owner",
        )

    def final_accept(self, token: str) -> "DemoRunner":
        """Step 8: Human Owner accepts the FINAL_ACCEPTANCE gate."""
        if self.state != DemoState.FINAL_ACCEPTANCE_READY:
            if self.state in (
                DemoState.CI_PENDING,
                DemoState.DELIVERED,
                DemoState.COMMIT_VERIFIED,
                DemoState.INIT,
            ):
                code = "ACCEPTANCE_BEFORE_CI"
            else:
                code = "ACCEPTANCE_REJECTED"
            raise DemoRejected(
                code,
                f"final_accept requires FINAL_ACCEPTANCE_READY, current={self.state}",
            )
        if not hmac.compare_digest(str(token), str(self.human_owner_token)):
            raise DemoRejected(
                "ACCEPTANCE_TOKEN_MISMATCH",
                "Human Owner acceptance token mismatch",
            )
        self.accepted = True
        return self._set_state(
            DemoState.ACCEPTED, message="Human Owner accepted FINAL_ACCEPTANCE"
        )

    def complete(self) -> "DemoRunner":
        """Steps 9-11: finalize, emit TASK_COMPLETED and the structured summary."""
        if self.state != DemoState.ACCEPTED:
            raise DemoRejected(
                "COMPLETION_BEFORE_ACCEPTANCE",
                f"complete() requires ACCEPTED, current={self.state}",
            )
        self.completed = True
        self.result = TASK_COMPLETED
        self._set_state(DemoState.COMPLETED, message="TASK_COMPLETED")
        if self.summary_path:
            self.write_summary_json(self.summary_path)
        if self.verbose:
            print(TASK_COMPLETED)
        return self

    def run_to_completion(self, token: str) -> "DemoRunner":
        """Convenience: run -> final_accept -> complete (happy path only)."""
        self.run()
        if self.state != DemoState.FINAL_ACCEPTANCE_READY:
            return self
        self.final_accept(token)
        self.complete()
        return self

    # -- structured status summary (readable by "Buzz") ----------------------
    def summary(self) -> dict:
        """Machine-readable status digest for an external reader agent."""
        dstatus = self.delivery_status
        return {
            "schema": SUMMARY_SCHEMA,
            "demo_name": DEMO_NAME,
            "mode": self.mode,
            "task_id": self.task_id,
            "repository": self.repository,
            "state": self.state,
            "result": self.result,
            "delivery_state": dstatus.state if dstatus else None,
            "delivery_status_code": dstatus.status_code if dstatus else None,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "ci_status": self.ci_status,
            "accepted": self.accepted,
            "completed": self.completed,
            "rejection": self.rejection,
            "message": self.message,
            "local_commit_sha": self.local_commit_sha,
            "expected_sha": self.expected_sha,
            "ts": time.time(),
        }

    def write_summary_json(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.summary(), fh, indent=2, sort_keys=True)
        return path


# --------------------------------------------------------------------------
# Live broker provider (lazy import so offline never touches credentials).
# --------------------------------------------------------------------------
def _default_broker_provider():
    from hermes_worker.github_app import GitHubAppTokenBroker

    broker = GitHubAppTokenBroker()

    def provider(repository: str) -> str:
        return broker.mint_installation_token(repository)

    return provider


# --------------------------------------------------------------------------
# Factories
# --------------------------------------------------------------------------
def build_offline_runner(
    *,
    repository: str = SMOKE_REPO,
    task_id: str = "T1",
    worktree_path: str = "/dev/null/fake-ws",
    local_commit_sha: str = "a" * 40,
    expected_sha: str = "a" * 40,
    title: str = "demo task",
    body: str = "demo body",
    human_owner_token: str = "owner-token",
    git=None,
    git_ops=None,
    delivery_github=None,
    ci_client=None,
    token_provider=None,
    registry=None,
    ci_provider=None,
    verbose: bool = False,
    summary_path: _t.Optional[str] = None,
) -> DemoRunner:
    """Build a fully-offline, deterministic DemoRunner (fakes for everything)."""
    return DemoRunner(
        mode="offline",
        repository=repository,
        task_id=task_id,
        worktree_path=worktree_path,
        local_commit_sha=local_commit_sha,
        expected_sha=expected_sha,
        title=title,
        body=body,
        human_owner_token=human_owner_token,
        git=git,
        git_ops=git_ops,
        delivery_github=delivery_github,
        ci_client=ci_client,
        token_provider=token_provider,
        registry=registry,
        ci_provider=ci_provider,
        verbose=verbose,
        summary_path=summary_path,
    )


def build_live_smoke_runner(
    *,
    repository: str,
    task_id: str,
    worktree_path: str,
    local_commit_sha: str,
    expected_sha: str,
    title: str,
    body: str,
    human_owner_token: str,
    token_provider=None,
    delivery_github=None,
    ci_client=None,
    registry=None,
    git=None,
    git_ops=None,
    verbose: bool = False,
    summary_path: _t.Optional[str] = None,
) -> DemoRunner:
    """Build a live-smoke runner. Rejects any repo other than the smoke-test.

    Real Draft PRs go through ``GitHubRestClient``; CI status through
    ``RealGitHubClient``. Merge is never invoked. Credentials come from an
    injected ``token_provider`` (or the GitHub App broker, lazily).
    """
    if repository != SMOKE_REPO:
        raise DemoError(
            "LIVE_SMOKE_REPO_NOT_ALLOWED",
            f"live-smoke only permits {SMOKE_REPO}, got {repository!r}",
        )
    git = git or LocalGitWorkspaceInspector(worktree_path)
    git_ops = git_ops or HostGitOperations()
    delivery_github = delivery_github or GitHubRestClient()
    ci_client = ci_client or RealGitHubClient()
    token_provider = token_provider or _default_broker_provider()
    registry = registry or InMemoryDeliveryRegistry()
    return DemoRunner(
        mode="live-smoke",
        repository=repository,
        task_id=task_id,
        worktree_path=worktree_path,
        local_commit_sha=local_commit_sha,
        expected_sha=expected_sha,
        title=title,
        body=body,
        human_owner_token=human_owner_token,
        git=git,
        git_ops=git_ops,
        delivery_github=delivery_github,
        ci_client=ci_client,
        token_provider=token_provider,
        registry=registry,
        verbose=verbose,
        summary_path=summary_path,
    )


def main(argv: _t.Optional[list] = None) -> int:  # pragma: no cover - CLI glue
    """Minimal offline self-demo so the tool is runnable end-to-end.

    Runs the happy path (CI forced green) and prints the structured summary.
    """
    parser = None
    try:
        import argparse

        p = argparse.ArgumentParser(description=DEMO_NAME)
        p.add_argument("--mode", choices=("offline", "live-smoke"), default="offline")
        p.add_argument("--repository", default=SMOKE_REPO)
        p.add_argument("--task-id", default="T1")
        p.add_argument("--worktree-path", default="/dev/null/fake-ws")
        p.add_argument("--local-commit-sha", default="a" * 40)
        p.add_argument("--expected-sha", default="a" * 40)
        p.add_argument("--title", default="ONE-DAY collaboration demo task")
        p.add_argument("--body", default="Automated demo delivery.")
        p.add_argument("--human-owner-token", default="owner-token")
        p.add_argument("--summary-path", default=None)
        args = p.parse_args(argv)
        parser = args
    except Exception:
        parser = None

    if parser is None:  # argparse unavailable -> safe offline defaults
        runner = build_offline_runner(verbose=True, ci_provider=lambda pr: "success")
    elif parser.mode == "live-smoke":
        runner = build_live_smoke_runner(
            repository=parser.repository,
            task_id=parser.task_id,
            worktree_path=parser.worktree_path,
            local_commit_sha=parser.local_commit_sha,
            expected_sha=parser.expected_sha,
            title=parser.title,
            body=parser.body,
            human_owner_token=parser.human_owner_token,
            verbose=True,
            summary_path=parser.summary_path,
        )
    else:
        runner = build_offline_runner(
            repository=parser.repository,
            task_id=parser.task_id,
            worktree_path=parser.worktree_path,
            local_commit_sha=parser.local_commit_sha,
            expected_sha=parser.expected_sha,
            title=parser.title,
            body=parser.body,
            human_owner_token=parser.human_owner_token,
            verbose=True,
            summary_path=parser.summary_path,
            ci_provider=lambda pr: "success",
        )

    runner.run_to_completion(parser.human_owner_token if parser else "owner-token")
    print(json.dumps(runner.summary(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
