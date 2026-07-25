"""Independent Reviewer (D3 requirement D).

Runs as a SEPARATE role from the coding Agent. It reads the PR diff + sandbox
evidence and returns a STRUCTURED verdict. It must NEVER reuse the coding
Agent's own conclusions (the Agent may not self-approve). Blocking findings
trigger a round-2 rework; non-blocking findings are recorded but do not cause
infinite rework. The loop stops after ``MAX_ROUNDS`` rounds and escalates to
the user.

The decision is driven by an injected ``decide`` callable so it is deterministic
and offline-testable. In production ``decide`` calls a model through the relay
adapter in an isolated process (ROLE_REVIEWER); here a scriptable rule stands in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .constants import ROLE_REVIEWER


@dataclass
class ReviewFinding:
    severity: str            # "blocking" | "non_blocking"
    title: str
    detail: str = ""


@dataclass
class ReviewVerdict:
    verdict: str             # "APPROVE" | "REQUEST_CHANGES"
    role: str = ROLE_REVIEWER
    findings: List[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    pr_number: Optional[int] = None

    @property
    def has_blocking(self) -> bool:
        return any(f.severity == "blocking" for f in self.findings)


class Reviewer:
    def __init__(self, decide: Optional[Callable] = None):
        # decide(pr_state: dict, evidence: dict) -> ReviewVerdict
        self._decide = decide or self._default_decide

    @staticmethod
    def _default_decide(pr_state: dict, evidence: dict) -> ReviewVerdict:
        findings: List[ReviewFinding] = []
        if evidence.get("ci_status") != "success":
            findings.append(ReviewFinding(
                "blocking", "CI not green",
                "Reviewer requires CI success before approval."))
        if evidence.get("agent_self_approved"):
            findings.append(ReviewFinding(
                "blocking", "Agent self-approval forbidden",
                "The coding Agent may not approve its own PR."))
        if evidence.get("secrets_in_diff"):
            findings.append(ReviewFinding(
                "blocking", "Secret in diff",
                "Potential credential leaked in the PR diff."))
        if evidence.get("lint_warnings"):
            findings.append(ReviewFinding(
                "non_blocking", "Lint warnings",
                "Non-blocking lint warnings present."))
        verdict = "REQUEST_CHANGES" if any(
            f.severity == "blocking" for f in findings) else "APPROVE"
        return ReviewVerdict(verdict, findings=findings, summary="auto verdict")

    def review(self, pr_state: dict, evidence: dict) -> ReviewVerdict:
        v = self._decide(pr_state, evidence)
        v.role = ROLE_REVIEWER
        v.pr_number = pr_state.get("number")
        return v
