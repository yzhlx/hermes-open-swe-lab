"""Release Agent — the ONLY production caller of ``DeliveryController.deliver()``.

PB-1 critical path (single, controlled delivery path):

    Coding Worker  -> local commit (commit_sha)
        -> ReleaseAgent
            -> DeliveryController.deliver()     (reused verbatim from delivery.py)
                -> controlled Push  -> idempotent Draft PR

Why this layer exists
----------------------
Before PB-1 there were two delivery paths: the controlled ``DeliveryController``
AND a direct ``WorkerAgent`` push/create_draft_pr. That dual path is now removed.
The Coding Worker (``WorkerAgent``) is forbidden from pushing, from opening
Draft PRs, from holding delivery credentials, and from calling
``DeliveryController.deliver()`` directly. The Release Agent is the single
production role that performs delivery.

Hard boundaries (enforced here, not just documented)
----------------------------------------------------
* The Release Agent NEVER runs ``git push`` itself.
* It NEVER calls the GitHub Draft-PR API directly.
* It NEVER executes Coding-Worker code.
* It NEVER holds a long-lived GitHub token: the token is minted inside
  ``DeliveryController.deliver()`` via its ``token_provider`` and discarded.
* ``delivery.py`` is reused byte-for-byte; this module adds NO second
  push/PR implementation.

The Release Agent records the canonical delivery events on the
``ControlPlane`` event store so the rest of the system (Scheduler CI/Review)
can follow the same PR:

    push_completed / draft_pr_created / ci_pending
    (+ delivery_idempotent_reuse / delivery_blocked for non-happy paths)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .constants import ROLE_RELEASE_AGENT
from .control_plane import ControlPlane
from .delivery import (
    DeliveryController,
    DeliveryAuthorization,
    DeliveryStatus,
    DUPLICATE_SUPPRESSED,
)


# --------------------------------------------------------------------------
# Event types recorded on the ControlPlane event store.
# --------------------------------------------------------------------------
EVENT_PUSH_COMPLETED = "push_completed"
EVENT_DRAFT_PR_CREATED = "draft_pr_created"
EVENT_CI_PENDING = "ci_pending"
EVENT_DELIVERY_IDEMPOTENT = "delivery_idempotent_reuse"
EVENT_DELIVERY_BLOCKED = "delivery_blocked"

# Delivery source tag written on every delivery event (single source of truth).
DELIVERY_SOURCE = "github"

# A *real* git commit SHA is exactly 40 lowercase hex characters. The Coding
# Worker must hand off a commit that actually exists in the repo; the literal
# string "simulated" or any other placeholder is explicitly NOT a delivery.
_REAL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class ReleaseResult:
    """Outcome of a single ``deliver_task`` call."""

    pr_number: Optional[int]
    pr_url: Optional[str]
    commit_sha: str
    status_code: str
    completed: bool
    idempotent: bool = False
    message: str = ""


class ReleaseAgent:
    """Owns the single production delivery entry point.

    Construction
    ------------
    Receives:
      * ``cp``           — ControlPlane (event store + job state).
      * ``delivery``     — an EXISTING ``DeliveryController`` (already wired with
                           git operations, github client, and ``token_provider``).
      * ``repository``   — the target repo for this agent's deliveries.
      * ``token_broker`` — optional authorization source; only used to wire the
                           controller's ``token_provider`` if it is not already
                           set. The Release Agent does NOT store the token.

    It must NOT be given a free GitHub client that can push or open PRs — those
    live inside the ``DeliveryController`` (``HostGitOperations`` / GitHub REST
    client), which this agent drives exclusively through ``deliver()``.
    """

    def __init__(self, cp: ControlPlane, delivery: DeliveryController,
                 repository: str, token_broker=None,
                 default_base_branch: str = "main"):
        self.cp = cp
        self.delivery = delivery
        self.repository = repository
        self.token_broker = token_broker
        self.default_base_branch = default_base_branch
        # If the controller was constructed without a token provider but a
        # broker is available, wire the broker's short-lived minting as the
        # provider. The token is still minted per-call inside deliver() and
        # never retained by this agent.
        if self.delivery.token_provider is None and token_broker is not None:
            self.delivery.token_provider = (
                lambda repo: token_broker.mint_installation_token(repo))

    # -- authorization ----------------------------------------------------
    def _authorize(self, task_id: str, repository: str,
                   commit_sha: str) -> DeliveryAuthorization:
        """Build the explicit, task+repo+commit-scoped grant.

        The Coding Worker never builds this object — only the Release Agent may.
        """
        return DeliveryAuthorization(
            task_id=task_id, repository=repository, commit_sha=commit_sha,
            grant=DeliveryAuthorization.REQUIRED_GRANT)

    # -- delivery ---------------------------------------------------------
    def deliver_task(self, job_id: int, task_id: str, repository: str,
                     commit_sha: str, expected_sha: str, title: str, body: str,
                     test_summary: str, security_summary: str,
                     base_branch: Optional[str] = None,
                     issue_number: Optional[int] = None,
                     remote_branch: Optional[str] = None) -> ReleaseResult:
        """Deliver a completed local commit as a controlled Draft PR.

        Returns a :class:`ReleaseResult`. On success the canonical delivery
        events are recorded on the event store. On a mismatch / block the call
        records a ``delivery_blocked`` event and NEVER a fake success.
        """
        base_branch = base_branch or self.default_base_branch
        authz = self._authorize(task_id, repository, commit_sha)

        status: DeliveryStatus = self.delivery.deliver(
            task_id=task_id, repository=repository,
            local_commit_sha=commit_sha, expected_sha=expected_sha,
            authorization=authz, title=title, body=body,
            test_summary=test_summary, security_summary=security_summary,
            base_branch=base_branch, issue_number=issue_number,
            remote_branch=remote_branch)

        completed = status.completed
        idempotent = status.status_code == DUPLICATE_SUPPRESSED
        pr_number = status.pr_number
        pr_url = status.pr_url

        if completed and pr_number is not None:
            # Canonical happy-path events. These let the Scheduler's CI/Review
            # flow follow the SAME PR.
            self.cp.append_event(job_id, {
                "type": EVENT_PUSH_COMPLETED,
                "payload": {
                    "job_id": job_id, "task_id": task_id,
                    "repository": repository, "commit_sha": commit_sha,
                    "source": DELIVERY_SOURCE,
                }})
            self.cp.append_event(job_id, {
                "type": EVENT_DRAFT_PR_CREATED,
                "payload": {
                    "job_id": job_id, "task_id": task_id,
                    "repository": repository, "commit_sha": commit_sha,
                    "pr_number": pr_number, "pr_url": pr_url or "",
                    "source": DELIVERY_SOURCE,
                }})
            self.cp.append_event(job_id, {
                "type": EVENT_CI_PENDING,
                "payload": {
                    "job_id": job_id, "task_id": task_id,
                    "repository": repository, "commit_sha": commit_sha,
                    "pr_number": pr_number,
                    "delivered_commit_sha": commit_sha,
                    "source": DELIVERY_SOURCE,
                }})
            self.cp.store_agent_result(job_id, {
                "pr_number": pr_number,
                "pr_url": pr_url or "",
                "commit_sha": commit_sha,
                "delivered_commit_sha": commit_sha,
                "ci_status": "pending",
                "role": ROLE_RELEASE_AGENT,
            })
        elif idempotent and pr_number is not None:
            # Same task+repo+commit already delivered: reuse the existing PR,
            # do NOT create a second one and do NOT push again.
            self.cp.append_event(job_id, {
                "type": EVENT_DELIVERY_IDEMPOTENT,
                "payload": {
                    "job_id": job_id, "task_id": task_id,
                    "repository": repository, "commit_sha": commit_sha,
                    "pr_number": pr_number, "source": DELIVERY_SOURCE,
                }})
            self.cp.store_agent_result(job_id, {
                "pr_number": pr_number,
                "commit_sha": commit_sha,
                "delivered_commit_sha": commit_sha,
                "ci_status": "pending",
                "role": ROLE_RELEASE_AGENT,
            })
        else:
            # Blocked / failed: record the block and NEVER a fake success.
            self.cp.append_event(job_id, {
                "type": EVENT_DELIVERY_BLOCKED,
                "payload": {
                    "job_id": job_id, "task_id": task_id,
                    "repository": repository, "commit_sha": commit_sha,
                    "status_code": status.status_code,
                    "source": DELIVERY_SOURCE,
                }})
        return ReleaseResult(
            pr_number=pr_number, pr_url=pr_url, commit_sha=commit_sha,
            status_code=status.status_code, completed=completed,
            idempotent=idempotent, message=status.message)

    # -- self-checks used by structural tests -----------------------------
    @staticmethod
    def is_real_commit_sha(commit_sha) -> bool:
        """True only for a genuine 40-char lowercase-hex git SHA.

        The literal ``"simulated"`` and any placeholder are rejected so they can
        never be handed off as a real delivery commit.
        """
        return (bool(commit_sha)
                and commit_sha != "simulated"
                and _REAL_SHA_RE.match(commit_sha or "") is not None)
