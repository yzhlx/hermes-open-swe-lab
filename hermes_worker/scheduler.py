"""D3 workflow state machine + orchestration (D3 requirement C).

Composes the existing primitives (``ControlPlane`` queue/lease/event-store,
``WebhookReceiver``/``EventRouter`` routing, ``HermesDockerSandboxBackend``
isolation, provider-live ``RelayClient``) with the new pieces (``GitHubAppTokenBroker``,
``GitHubClient``, ``AgentRunner``, ``Reviewer``) into the closed loop:

    Issue → Job → Worker → Sandbox → Agent → Commit → Push → Draft PR
          → CI → Reviewer → round-2 → 2nd Commit → Re-review → User Acceptance

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

    WorkerAgent  (ROLE_CODING_AGENT)  -- runs the agent, pushes, opens Draft PR
    Scheduler    (ROLE_SCHEDULER)     -- polls CI, invokes Reviewer, owns round-2
    Reviewer     (ROLE_REVIEWER)      -- independent verdict, never reuses agent output
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .control_plane import (
    ControlPlane,
    ControlPlaneError,
    is_trusted_scheduler_event,
)
from .constants import (
    ALLOWED_GITHUB_REPOS, ROLE_CODING_AGENT, ROLE_REVIEWER, ROLE_SCHEDULER,
    ROUND2_LABEL, MAX_ROUNDS,
)
from .agent_runner import AgentRunner, AgentEvidence
from .redact import redact
from .reviewer import Reviewer, ReviewVerdict


def _branch_for(job_id: int) -> str:
    return f"hermes/task-{job_id}"


_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)


def _redact_review_text(value, limit: int) -> str:
    clean = redact(str(value or ""))
    clean = _PRIVATE_KEY_BLOCK_RE.sub(
        "[REDACTED PRIVATE KEY BLOCK]",
        clean,
    )
    return clean[:limit]


class WorkerAgent:
    """Runs the coding Agent inside the sandbox, pushes, opens the Draft PR.

    Role: ROLE_CODING_AGENT. Has NO access to label mutation — the Scheduler owns
    round-2 (D3 requirement #17). The GitHub token is obtained from the broker
    ONLY at push time and is never stored or logged.
    """
    def __init__(self, cp: ControlPlane, github, broker, agent: AgentRunner,
                 repo: str = "yzhlx/hermes-open-swe-smoke-test"):
        self.cp = cp
        self.github = github
        self.broker = broker
        self.agent = agent
        self.repo = repo

    def run_phase(self, job_id: int, worker_token: str, round: int,
                  instruction: str, sandbox) -> AgentEvidence:
        # 0) Claim / re-activate the task (idempotent; sets state=running so the
        #    lease-gated token broker will authorize this worker).
        self.cp.claim(worker_token)
        # 1) Agent edits the target repo working tree inside the sandbox.
        sandbox.create()
        self.github.clone(self.repo, sandbox._ws)
        ev = self.agent.run(sandbox, sandbox._ws, instruction, round=round)
        self.cp.append_event(job_id, {"type": "agent_run",
                                      "payload": {"round": round,
                                                  "commit_sha": ev.commit_sha}})

        # 2) Lease-gated token — ONLY delivered here, at push time (#7/#8).
        token = self.broker.get_token_for_job(self.cp, job_id, worker_token)
        self.cp.append_event(job_id, {"type": "token_issued",
                                      "payload": {"repo": self.repo,
                                                  "issued": True}})

        # 3) Push (token injected only for this push; never logged).
        branch = _branch_for(job_id)
        self.github.push_branch(self.repo, branch, ev.commit_sha, token=token)
        self.cp.append_event(job_id, {"type": "push",
                                      "payload": {"branch": branch,
                                                  "round": round}})

        # 4) PR lifecycle: round 1 opens a Draft PR; later rounds push the SAME PR.
        existing = self.cp.get_job(job_id).get("pr_number")
        if existing is None:
            pr = self.github.create_draft_pr(self.repo, branch,
                                             f"Hermes task {job_id} (round {round})")
            pr_number = pr["number"]
            self.cp.store_agent_result(job_id, {
                "pr_number": pr_number, "commit_sha": ev.commit_sha,
                "round": round, "ci_status": "pending",
                "modified_files": ev.modified_files, "token_usage": ev.token_usage,
                "model": ev.model, "role": ROLE_CODING_AGENT,
                "tool_calls": ev.tool_calls,
            })
            self.cp.append_event(job_id, {"type": "draft_pr",
                                          "payload": {"pr_number": pr_number,
                                                      "branch": branch}})
        else:
            # Reuse the same PR; update its head. No new PR is opened (#18).
            self.github.push_branch(self.repo, branch, ev.commit_sha, token=token)
            pr_number = existing
            self.cp.store_agent_result(job_id, {
                "commit_sha": ev.commit_sha, "round": round, "ci_status": "pending",
                "modified_files": ev.modified_files, "token_usage": ev.token_usage,
                "model": ev.model, "role": ROLE_CODING_AGENT,
                "tool_calls": ev.tool_calls,
            })
            self.cp.append_event(job_id, {"type": "round2_push",
                                          "payload": {"pr_number": pr_number,
                                                      "branch": branch,
                                                      "new_head": ev.commit_sha}})

        self.cp.set_state(job_id, "agent_done")
        return ev


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
        """The ONLY path that adds and durably records the round-2 label."""
        self.github.add_label(pr_number, ROUND2_LABEL)
        self.cp.record_round2_signal(job_id, pr_number)

    def _pending_round2_review(self, job_id: int) -> Optional[dict]:
        """Return a trusted request-changes review not yet consumed."""
        events = sorted(
            self.cp.get_events(job_id),
            key=lambda event: int(event["id"]),
        )
        delivery_events = [
            event
            for event in events
            if event.get("event_type") in {"pr_created", "round2_push"}
        ]
        latest_delivery = delivery_events[-1] if delivery_events else None
        last_delivery_id = (
            int(latest_delivery["id"]) if latest_delivery else 0
        )
        reviews = [
            event
            for event in events
            if int(event["id"]) > last_delivery_id
            and is_trusted_scheduler_event(
                event,
                "review",
                ROLE_REVIEWER,
            )
        ]
        if not reviews:
            return None
        review = reviews[-1]
        try:
            payload = json.loads(review.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        findings = payload.get("findings") or []
        has_blocking = any(
            isinstance(finding, dict)
            and str(finding.get("severity", "")).lower() == "blocking"
            for finding in findings
        )
        if payload.get("verdict") != "REQUEST_CHANGES" and not has_blocking:
            return None
        review_id = int(review["id"])
        label_recorded = any(
            int(event["id"]) > review_id
            and is_trusted_scheduler_event(
                event,
                "round2_label",
                ROLE_SCHEDULER,
            )
            for event in events
        )
        return {
            "payload": payload,
            "label_recorded": label_recorded,
            "delivery_type": (
                latest_delivery.get("event_type")
                if latest_delivery
                else None
            ),
        }

    def _record_escalation(
        self,
        job_id: int,
        pr_number: int,
        round_number: int,
    ) -> None:
        self.cp.set_state(job_id, "escalated")
        self.cp.append_event(job_id, {
            "id": f"scheduler:{job_id}:escalated:{round_number}",
            "type": "escalated",
            "payload": {
                "pr_number": pr_number,
                "round": round_number,
            },
            "source_type": "scheduler",
            "source_id": ROLE_SCHEDULER,
            "actor_role": ROLE_SCHEDULER,
        })

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
        if job.get("state") == "escalated":
            return {"action": "escalated", "replayed": True}
        pr_number = job.get("pr_number")
        if ci_status != "success":
            # CI failure: loop back to the agent for a fix round (same PR).
            # The Reviewer is NOT invoked on a red CI (#14).
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {"type": "ci_fail",
                                          "payload": {"ci_status": ci_status}})
            return {"action": "ci_fail"}

        pr_state = self.github.get_pr(pr_number) if pr_number else {}
        expected_head = job.get("commit_sha")
        actual_head = (
            pr_state.get("head_sha") if isinstance(pr_state, dict) else None
        )
        if not expected_head or expected_head != actual_head:
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {
                "type": "head_mismatch",
                "payload": {
                    "expected_head": expected_head,
                    "actual_head": actual_head,
                    "pr_number": pr_number,
                },
                "source_type": "github",
                "source_id": str(pr_number or "unverified"),
            })
            return {"action": "head_mismatch"}

        try:
            actual_ci_status = (
                self.github.get_ci_status(
                    pr_number,
                    expected_head=actual_head,
                )
                if pr_number
                else None
            )
        except Exception:  # noqa: BLE001 - missing/unreadable fact fails closed
            actual_ci_status = None
        if actual_ci_status != "success":
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {
                "type": "ci_fail",
                "payload": {
                    "requested_ci_status": ci_status,
                    "actual_ci_status": actual_ci_status,
                },
                "source_type": "github",
                "source_id": str(pr_number or "unverified"),
            })
            return {"action": "ci_fail", "ci_status": actual_ci_status}

        try:
            post_ci_pr_state = self.github.get_pr(pr_number) if pr_number else {}
        except Exception:  # noqa: BLE001 - an unverifiable head fails closed
            post_ci_pr_state = {}
        post_ci_head = (
            post_ci_pr_state.get("head_sha")
            if isinstance(post_ci_pr_state, dict)
            else None
        )
        if post_ci_head != actual_head:
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {
                "type": "head_mismatch",
                "payload": {
                    "expected_head": expected_head,
                    "actual_head": post_ci_head,
                    "observed_head_before_ci": actual_head,
                    "pr_number": pr_number,
                },
                "source_type": "github",
                "source_id": str(pr_number or "unverified"),
            })
            return {"action": "head_mismatch"}

        pending_round2 = self._pending_round2_review(job_id)
        if pending_round2 is not None:
            if pending_round2["delivery_type"] == "round2_push":
                round_number = int(job.get("round") or MAX_ROUNDS)
                self._record_escalation(
                    job_id,
                    pr_number,
                    round_number,
                )
                return {
                    "action": "escalated",
                    "review": pending_round2["payload"],
                    "replayed": True,
                }
            if not pending_round2["label_recorded"]:
                self.add_round2_label(job_id, pr_number)
            return {
                "action": "rework",
                "round": 2,
                "review": pending_round2["payload"],
                "replayed": True,
            }

        evidence = {
            "ci_status": actual_ci_status,
            "agent_self_approved": False,     # Agent never approves its own PR (#16)
            "secrets_in_diff": False,
            "commit_sha": job.get("commit_sha"),
        }
        if evidence_extra:
            evidence.update(evidence_extra)
        verdict: ReviewVerdict = reviewer.review(pr_state, evidence)
        try:
            post_review_pr_state = (
                self.github.get_pr(pr_number) if pr_number else {}
            )
        except Exception:  # noqa: BLE001 - an unverifiable head fails closed
            post_review_pr_state = {}
        post_review_head = (
            post_review_pr_state.get("head_sha")
            if isinstance(post_review_pr_state, dict)
            else None
        )
        if post_review_head != actual_head:
            self.cp.set_state(job_id, "agent_done")
            self.cp.append_event(job_id, {
                "type": "head_mismatch",
                "payload": {
                    "expected_head": expected_head,
                    "actual_head": post_review_head,
                    "observed_head_before_review": actual_head,
                    "pr_number": pr_number,
                },
                "source_type": "github",
                "source_id": str(pr_number or "unverified"),
            })
            return {"action": "head_mismatch"}
        has_blocking = any(
            str(f.severity).lower() == "blocking" for f in verdict.findings
        )
        effective_verdict = (
            "APPROVE"
            if verdict.verdict == "APPROVE" and not has_blocking
            else "REQUEST_CHANGES"
        )
        ordered_findings = sorted(
            enumerate(verdict.findings),
            key=lambda item: (
                str(item[1].severity).lower() != "blocking",
                item[0],
            ),
        )
        selected_findings = [
            finding for _, finding in ordered_findings[:50]
        ]
        findings = [
            {
                "severity": _redact_review_text(finding.severity, 64),
                "title": _redact_review_text(finding.title, 500),
                "detail": _redact_review_text(finding.detail, 4000),
            }
            for finding in selected_findings
        ]
        self.cp.append_event(job_id, {
            "type": "review",
            "payload": {
                "verdict": effective_verdict,
                "summary": _redact_review_text(verdict.summary, 4000),
                "findings": findings,
                "findings_truncated": max(
                    0, len(verdict.findings) - len(selected_findings)
                ),
            },
            "source_type": "scheduler",
            "source_id": ROLE_SCHEDULER,
            "actor_role": verdict.role,
        })

        if effective_verdict == "APPROVE":
            self.cp.set_state(job_id, "await_user")
            self.cp.append_event(job_id, {"type": "await_user",
                                          "payload": {"pr_number": pr_number}})
            return {"action": "await_user", "verdict": verdict}

        # REQUEST_CHANGES
        round_now = job.get("round") or 1
        if round_now >= MAX_ROUNDS:
            self._record_escalation(job_id, pr_number, int(round_now))
            return {"action": "escalated", "verdict": verdict}
        self.add_round2_label(job_id, pr_number)
        return {"action": "rework", "round": round_now + 1, "verdict": verdict}


class D3Orchestrator:
    """Coordinates WorkerAgent + Scheduler + Reviewer into the closed loop."""

    def __init__(self, cp: ControlPlane, github, broker, agent: AgentRunner,
                 reviewer: Reviewer, sandbox,
                 repo: str = "yzhlx/hermes-open-swe-smoke-test"):
        self.cp = cp
        self.github = github
        self.broker = broker
        self.agent = agent
        self.reviewer = reviewer
        self.sandbox = sandbox
        self.repo = repo
        self.worker = WorkerAgent(cp, github, broker, agent, repo)
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
            # ``ci_status`` is an offline-test injection supported only by the
            # in-memory fake.  Production clients must report their own GitHub
            # fact and are never overwritten by caller input.
            from .github_client import FakeGitHubClient
            if isinstance(self.github, FakeGitHubClient):
                current_job = self.cp.get_job(job_id)
                current_pr = current_job.get("pr_number")
                if current_pr:
                    pr_state = self.github.prs.get(current_pr)
                    if pr_state:
                        branch = pr_state.get("branch")
                        branch_head = (
                            self.github.repos
                            .get(self.repo, {})
                            .get("branches", {})
                            .get(branch)
                        )
                        if branch_head is not None:
                            pr_state["head_sha"] = branch_head
                    self.github.set_ci_status(current_pr, ci_status)
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
