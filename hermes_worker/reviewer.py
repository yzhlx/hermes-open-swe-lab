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

An optional Relay LLM review path is enabled only with ``HERMES_REVIEWER_LLM=1``.
It is OFF by default and always falls back to the deterministic rules when the
relay is unavailable or returns no unambiguous verdict.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .constants import (
    ROLE_REVIEWER, ALLOWED_GITHUB_REPOS, HERMES_REVIEWER_LLM_ENV,
    DEFAULT_HERMES_REVIEWER_LLM,
)
from .agent_runner import build_relay_client
from .github_client import GitHubClientError, require_allowed_repo
from .redact import redact


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
    def __init__(self, decide: Optional[Callable] = None,
                 repo: Optional[str] = None,
                 allowed_repos=None, relay_client=None):
        # decide(pr_state: dict, evidence: dict) -> ReviewVerdict
        self._decide_override = decide
        self._relay = relay_client
        self._llm_enabled = os.environ.get(
            HERMES_REVIEWER_LLM_ENV, DEFAULT_HERMES_REVIEWER_LLM) == "1"
        # PB-6: bind the reviewer to a single allowed repository so it can never
        # read evidence or submit a review for a repo outside ALLOWED_GITHUB_REPOS.
        self._repo = repo
        self._allowed = set(allowed_repos) if allowed_repos is not None \
            else set(ALLOWED_GITHUB_REPOS)

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

    @staticmethod
    def _parse_llm_verdict(content: str) -> Optional[str]:
        """Accept only a standalone structured verdict from an LLM response."""
        normalized = (content or "").strip().upper()
        if normalized == "APPROVE":
            return "APPROVE"
        if normalized == "REQUEST_CHANGES":
            return "REQUEST_CHANGES"
        return None

    def _llm_decide(self, pr_state: dict, evidence: dict) -> Optional[ReviewVerdict]:
        """Return a relay verdict, or ``None`` so the caller uses rules.

        Relay errors and malformed outputs are deliberately non-fatal: the
        deterministic reviewer remains the safe decision path.
        """
        try:
            relay = self._relay or build_relay_client()
            prompt = redact(
                "You are the independent Hermes code reviewer. Return exactly "
                "one token: APPROVE or REQUEST_CHANGES.\n"
                f"PR state: {pr_state!r}\nEvidence: {evidence!r}"
            )
            response = relay.complete(
                use_responses=False,
                messages=[{"role": "user", "content": prompt}],
                model=relay.config.model,
            )
            verdict = self._parse_llm_verdict(relay.extract_content(response) or "")
            if verdict is None:
                return None
            return ReviewVerdict(verdict, summary="relay llm verdict")
        except Exception:
            return None

    def _decide(self, pr_state: dict, evidence: dict) -> ReviewVerdict:
        if self._decide_override is not None:
            return self._decide_override(pr_state, evidence)
        if self._llm_enabled:
            llm_verdict = self._llm_decide(pr_state, evidence)
            if llm_verdict is not None:
                return llm_verdict
        return self._default_decide(pr_state, evidence)

    def review(self, pr_state: dict, evidence: dict) -> ReviewVerdict:
        # PB-6: before reading evidence or deciding, verify the bound repo is
        # allow-listed. Unallowed / protected / unconfigured repos fail closed
        # with repo_not_allowed and NEVER reach a model or a GitHub write.
        if self._repo is not None:
            require_allowed_repo(self._repo, allowed=self._allowed)
        v = self._decide(pr_state, evidence)
        v.role = ROLE_REVIEWER
        v.pr_number = pr_state.get("number")
        return v
