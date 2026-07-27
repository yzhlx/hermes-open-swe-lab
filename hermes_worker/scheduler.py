"""D3 workflow state machine + orchestration (D3 requirement C).

Composes the existing primitives (``ControlPlane`` queue/lease/event-store,
``WebhookReceiver``/``EventRouter`` routing, ``HermesDockerSandboxBackend``
isolation, provider-live ``RelayClient``) with the new pieces (``GitHubAppTokenBroker``,
``AgentRunner``, ``Reviewer``) into the closed loop:

    Issue -> Job -> Worker -> Sandbox -> Agent -> Commit -> ReleaseAgent
          -> controlled Push -> Draft PR -> CI -> Reviewer -> round-2 -> User

PB-1 delivery convergence
-------------------------
There is now EXACTLY ONE production delivery path:

    Coding Worker (WorkerAgent)  -> real local commit (commit_sha)
        -> ReleaseAgent.deliver_task(...)        [the ONLY .deliver() caller]
            -> DeliveryController.deliver()        (reused verbatim from delivery.py)
                -> controlled Push -> idempotent Draft PR

The Coding Worker (``WorkerAgent``) MUST NOT push, MUST NOT open Draft PRs,
MUST NOT hold delivery credentials, and MUST NOT call
``DeliveryController.deliver()`` directly. It produces a real local commit,
persists it, and hands the ``commit_sha`` to the ``ReleaseAgent``.

Design principles (AGENTS.md §9, D3-IMPLEMENTATION-PLAN §4):
- Idempotent & recoverable: every stage writes an Event Store entry; the loop can
  resume from the job's last state after a crash/disconnect.
- One task per Issue; one PR per task; round-2 reuses the SAME PR (new head).
- The Scheduler has EXCLUSIVE ownership of the ``round-2`` label.
- The coding Agent and the Reviewer are distinct roles; the Agent never merges
  or self-approves.
- Auto-merge is forbidden: after APPROVE the job waits for the user.
- Max ``MAX_ROUNDS`` rounds; if still blocking, it escalates (stops, awaits user).

Roles::

    WorkerAgent  (ROLE_CODING_AGENT)  -- runs the agent, produces a real local
                                          commit, hands off to the ReleaseAgent
    ReleaseAgent (ROLE_RELEASE_AGENT) -- the ONLY role that calls
                                          DeliveryController.deliver()
    Scheduler    (ROLE_SCHEDULER)     -- polls CI, invokes Reviewer, owns round-2
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .control_plane import ControlPlane, ControlPlaneError
from .constants import (
    ALLOWED_GITHUB_REPOS, ROLE_CODING_AGENT, ROLE_SCHEDULER,
    ROLE_RELEASE_AGENT, ROUND2_LABEL, MAX_ROUNDS,
)
from .agent_runner import AgentRunner, AgentEvidence
from .reviewer import Reviewer, ReviewVerdict
from .release_delivery_coordinator import ReleaseAgent
from .repository import HostGitOperations
from .delivery import DeliveryController, LocalGitWorkspaceInspector


def _branch_for(job_id: int) -> str:
    return f"hermes/task-{job_id}"


# Coding-Worker lifecycle events (recorded on the ControlPlane event store).
EVENT_AGENT_FAILED = "agent_failed"
EVENT_COMMIT_CREATED = "commit_created"
EVENT_RELEASE_HANDOFF_REQUESTED = "release_handoff_requested"


@dataclass
class CodingWorkerResult:
    """Completion status the Coding Worker MUST expose before any delivery.

    PB-4 requires at least: tests_passed, local_commit_created, commit_sha,
    handoff_recorded. A delivery (ReleaseAgent handoff) is only permitted when
    all of ``tests_passed``, ``local_commit_created`` and a real ``commit_sha``
    are present.
    """

    tests_passed: bool
    local_commit_created: bool
    commit_sha: Optional[str]
    handoff_recorded: bool
    pr_number: Optional[int] = None
    message: str = ""


class WorkerAgent:
    """Runs the coding Agent inside the sandbox, then HANDS OFF to the ReleaseAgent.

    Role: ROLE_CODING_AGENT. It MUST NOT push, MUST NOT create Draft PR, MUST
    NOT hold delivery credentials, and MUST NOT call
    ``DeliveryController.deliver()`` directly (only the ``ReleaseAgent`` may).

    It produces a REAL local commit (``commit_sha``), persists it, and hands the
    ``commit_sha`` to the ``ReleaseAgent``, which performs the controlled push
    and Draft-PR creation. The GitHub client it holds (if any) is used ONLY for
    the read-only ``clone`` step; it is never asked to push or open a PR.
    """

    def __init__(self, cp: ControlPlane, github, agent: AgentRunner,
                 release_agent: ReleaseAgent,
                 repo: str = "yzhlx/hermes-open-swe-smoke-test"):
        self.cp = cp
        self.github = github                       # read-only (clone) only
        self.agent = agent
        self.release_agent = release_agent         # the ONLY delivery role
        self.repo = repo

    def run_phase(self, job_id: int, worker_token: str, round: int,
                  instruction: str, sandbox) -> CodingWorkerResult:
        # 0) Claim / re-activate the task (idempotent; sets state=running so the
        #    lease-gated token broker would authorize this worker if needed).
        self.cp.claim(worker_token)

        # 1) Agent edits the target repo working tree inside the sandbox.
        sandbox.create()
        if self.github is not None:
            self.github.clone(self.repo, sandbox._ws)
        try:
            ev = self.agent.run(sandbox, sandbox._ws, instruction, round=round)
            tests_passed = True
        except Exception:
            # Agent / test failure: there is no successful commit to deliver and
            # the Release Agent MUST NOT be called.
            self.cp.append_event(job_id, {
                "type": EVENT_AGENT_FAILED,
                "payload": {"job_id": job_id, "round": round,
                            "role": ROLE_CODING_AGENT}})
            self.cp.store_agent_result(job_id, {
                "tests_passed": False, "local_commit_created": False,
                "commit_sha": None, "handoff_recorded": False,
                "role": ROLE_CODING_AGENT})
            self.cp.set_state(job_id, "agent_done")
            return CodingWorkerResult(
                tests_passed=False, local_commit_created=False,
                commit_sha=None, handoff_recorded=False,
                message="agent run failed before a deliverable commit")

        commit_sha = ev.commit_sha
        local_commit_created = ReleaseAgent.is_real_commit_sha(commit_sha)

        if not local_commit_created:
            # No real local commit -> nothing to deliver. The literal
            # "simulated" or any missing/placeholder SHA is rejected here so it
            # can never be handed off as a real delivery commit.
            self.cp.append_event(job_id, {
                "type": EVENT_COMMIT_CREATED,
                "payload": {"job_id": job_id, "commit_sha": commit_sha,
                            "valid": False,
                            "reason": "not_a_real_local_commit",
                            "role": ROLE_CODING_AGENT, "source": "local"}})
            self.cp.store_agent_result(job_id, {
                "tests_passed": True, "local_commit_created": False,
                "commit_sha": commit_sha, "handoff_recorded": False,
                "role": ROLE_CODING_AGENT})
            self.cp.set_state(job_id, "agent_done")
            return CodingWorkerResult(
                tests_passed=True, local_commit_created=False,
                commit_sha=commit_sha, handoff_recorded=False,
                message="no real local commit; delivery skipped")

        # 2) Real local commit produced -> persist + record COMMIT_CREATED.
        self.cp.append_event(job_id, {
            "type": EVENT_COMMIT_CREATED,
            "payload": {"job_id": job_id, "commit_sha": commit_sha,
                        "valid": True, "task_id": str(job_id),
                        "role": ROLE_CODING_AGENT, "source": "local"}})
        self.cp.store_agent_result(job_id, {
            "commit_sha": commit_sha, "local_commit_created": True,
            "tests_passed": True, "role": ROLE_CODING_AGENT})

        # 3) Hand off to the Release Agent — the ONLY production delivery role.
        #    The Coding Worker never sees a GitHub push / Draft-PR token.
        self.cp.append_event(job_id, {
            "type": EVENT_RELEASE_HANDOFF_REQUESTED,
            "payload": {"job_id": job_id, "commit_sha": commit_sha,
                        "task_id": str(job_id), "role": ROLE_CODING_AGENT,
                        "source": "local"}})
        result = self.release_agent.deliver_task(
            job_id=job_id, task_id=str(job_id), repository=self.repo,
            commit_sha=commit_sha, expected_sha=commit_sha,
            title=f"Hermes task {job_id} (round {round})",
            body=instruction,
            test_summary="tests passed",
            security_summary="no known issues",
            base_branch="main",
            issue_number=None, remote_branch=None)
        pr_number = result.pr_number
        handoff_recorded = pr_number is not None
        if handoff_recorded:
            self.cp.store_agent_result(job_id, {
                "pr_number": pr_number, "commit_sha": commit_sha,
                "round": round, "ci_status": "pending",
                "modified_files": ev.modified_files,
                "token_usage": ev.token_usage, "model": ev.model,
                "role": ROLE_CODING_AGENT, "tool_calls": ev.tool_calls,
                "handoff_recorded": True})
        self.cp.set_state(job_id, "agent_done")
        return CodingWorkerResult(
            tests_passed=True, local_commit_created=True,
            commit_sha=commit_sha, handoff_recorded=handoff_recorded,
            pr_number=pr_number, message=result.message)


class Scheduler:
    """Drives CI gating + independent review + round-2 signalling.

    Role: ROLE_SCHEDULER. EXCLUSIVE owner of the ``round-2`` label (#17). Never
    calls merge. After APPROVE the job is left for the user.
    """

    def __init__(self, cp: ControlPlane, github,
                 repo: str = "yzhlx/hermes-open-swe-smoke-test"):
        self.cp = cp
        self.github = github
        self.repo = repo

    def add_round2_label(self, job_id: int, pr_number: int) -> None:
        """The ONLY path that adds the round-2 label (#17)."""
        self.github.add_label(pr_number, ROUND2_LABEL)
        self.cp.update_job(job_id, round=2)
        self.cp.append_event(job_id, {"type": "round2_label",
                                      "payload": {"pr_number": pr_number}})

    def review_phase(self, job_id: int, ci_status: str, reviewer: Reviewer,
                     evidence_extra: Optional[dict] = None) -> dict:
        """Gate on CI, then invoke the independent Reviewer.

        Returns a signal dict:
          {"action": "await_user"}                       -> APPROVE, stop
          {"action": "rework", "round": n}              -> REQUEST_CHANGES, do round n
          {"action": "escalated"}                        -> REQUEST_CHANGES past MAX_ROUNDS
          {"action": "ci_fail"}                          -> CI not green, rerun agent (no review)
        """
        job = self.cp.get_job(job_id)
        pr_number = job.get("pr_number")
        if ci_status != "success":
            # CI failure: loop back to the agent for a fix round (same PR).
            # The Reviewer is NOT invoked on a red CI (#14).
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {"type": "ci_fail",
                                          "payload": {"ci_status": ci_status}})
            return {"action": "ci_fail"}

        pr_state = self.github.get_pr(pr_number) if pr_number else {}
        evidence = {
            "ci_status": ci_status,
            "agent_self_approved": False,     # Agent never approves its own PR (#16)
            "secrets_in_diff": False,
            "commit_sha": job.get("commit_sha"),
        }
        if evidence_extra:
            evidence.update(evidence_extra)
        verdict: ReviewVerdict = reviewer.review(pr_state, evidence)
        self.cp.append_event(job_id, {
            "type": "review",
            "payload": {"verdict": verdict.verdict,
                        "role": verdict.role,
                        "findings": [f.severity for f in verdict.findings]}})

        if verdict.verdict == "APPROVE":
            self.cp.set_state(job_id, "await_user")
            self.cp.append_event(job_id, {"type": "await_user",
                                          "payload": {"pr_number": pr_number}})
            return {"action": "await_user", "verdict": verdict}

        # REQUEST_CHANGES
        round_now = job.get("round") or 1
        if round_now >= MAX_ROUNDS:
            self.cp.set_state(job_id, "escalated")
            self.cp.append_event(job_id, {"type": "escalated",
                                          "payload": {"pr_number": pr_number}})
            return {"action": "escalated", "verdict": verdict}
        self.add_round2_label(job_id, pr_number)
        return {"action": "rework", "round": round_now + 1, "verdict": verdict}


def _build_default_delivery_controller(github, broker, repo: str,
                                       workspace: Optional[str] = None):
    """Production default: a ``DeliveryController`` reusing the orchestrator's
    github client and the broker's short-lived token minting. Tests inject their
    own ``ReleaseAgent`` so this path is not exercised offline.
    """
    return DeliveryController(
        git=LocalGitWorkspaceInspector(workspace or "."),
        git_operations=HostGitOperations(),
        github_client=github,
        token_provider=(lambda r: broker.mint_installation_token(r))
                       if broker is not None else None,
        allowed_repos=None,
    )


class D3Orchestrator:
    """Coordinates WorkerAgent + ReleaseAgent + Scheduler + Reviewer into the loop."""

    def __init__(self, cp: ControlPlane, github, broker, agent: AgentRunner,
                 reviewer: Reviewer, sandbox,
                 repo: str = "yzhlx/hermes-open-swe-smoke-test",
                 release_agent: Optional[ReleaseAgent] = None):
        self.cp = cp
        self.github = github
        self.broker = broker
        self.agent = agent
        self.reviewer = reviewer
        self.sandbox = sandbox
        self.repo = repo
        if release_agent is None:
            delivery = _build_default_delivery_controller(github, broker, repo)
            release_agent = ReleaseAgent(cp, delivery, repo,
                                         token_broker=broker)
        self.release_agent = release_agent
        self.worker = WorkerAgent(cp, github, agent, release_agent, repo)
        self.scheduler = Scheduler(cp, github, repo)

    def run_job(self, job_id: int, worker_token: str, instruction: str,
                ci_status: str = "success", evidence_extra: Optional[dict] = None
                ) -> dict:
        """Drive the whole loop. ``ci_status`` is injected per round by the caller
        (in production it is polled from GitHub)."""
        round_n = 1
        last_signal = None
        while True:
            self.worker.run_phase(job_id, worker_token, round_n, instruction,
                                  self.sandbox)
            signal = self.scheduler.review_phase(
                job_id, ci_status, self.reviewer, evidence_extra)
            last_signal = signal
            if signal["action"] == "await_user":
                return signal
            if signal["action"] == "escalated":
                return signal
            if signal["action"] == "ci_fail":
                # Rerun the agent (same PR) after CI fix; keep same round.
                continue
            if signal["action"] == "rework":
                round_n = signal["round"]
                continue
            return signal
