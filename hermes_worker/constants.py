"""Shared allowlist + scope constants for Hermes Open SWE MVP-0.

Single source of truth for which GitHub repos automated operations may touch.
Mirrors the hard boundary in ``AGENTS.md`` Section 4 / D3-IMPLEMENTATION-PLAN.md
Section 3: only ``yzhlx/hermes-open-swe-smoke-test`` is ever touched; the
production ``yzhlx/hermes-learning-os`` and the cloud Hermes runtime are
protected and must never pass any allowlist check.
"""
from __future__ import annotations

# The ONLY repo automated GitHub operations may read/write.
ALLOWED_GITHUB_REPOS = {"yzhlx/hermes-open-swe-smoke-test"}

# Protected systems — never accessed, cloned, or modified (AGENTS.md §4).
PROTECTED_REPOS = {"yzhlx/hermes-learning-os"}

# Role names used to keep the coding Agent and the independent Reviewer isolated.
ROLE_CODING_AGENT = "coding_agent"
ROLE_REVIEWER = "reviewer"
ROLE_SCHEDULER = "scheduler"

# PB-1: the Release Agent is the ONLY production role permitted to call
# ``DeliveryController.deliver()``. The Coding Worker (WorkerAgent) and any
# other component must hand off to it; they must never push or open Draft PRs
# directly.
ROLE_RELEASE_AGENT = "release_agent"

# The single PR label a round-2 rework is signalled with. ONLY the scheduler may
# add it (D3-IMPLEMENTATION-PLAN.md §4.12).
ROUND2_LABEL = "round-2"

# Maximum rework rounds before the loop stops and escalates to the user.
MAX_ROUNDS = 2
# Host Codex Worker lifecycle. These states retain an active lease until a
# terminal state is written through ``ControlPlane.finish_state``.
HOST_WORKER_ACTIVE_STATES = (
    "running",
    "REPOSITORY_PREPARING",
    "CODEX_RUNNING",
    "TESTING",
    "COMMITTING",
    "PUSHING",
)

HOST_WORKER_TERMINAL_STATES = (
    "CODEX_FAILED",
    "CODEX_NO_CHANGES",
    "TEST_FAILED",
    "PR_CREATED",
    "BLOCKED",
)

# ---------------------------------------------------------------------------
# PB-23: Production Final Acceptance Gate (human-in-the-loop completion)
# ---------------------------------------------------------------------------
# CI statuses considered "green" (kept consistent with the demo runner's
# CI_GREEN so ported logic behaves identically).
CI_GREEN = ("success",)

# Event types for the final-acceptance chain. The Event Store is the SOLE
# source of truth — every completion-state transition is evidenced by one of
# these events, never by the auxiliary ``jobs.state`` column alone.
FINAL_ACCEPTANCE = "FINAL_ACCEPTANCE"
FINAL_ACCEPTED = "FINAL_ACCEPTED"
TASK_COMPLETED = "TASK_COMPLETED"

# Job state entered after a green CI emits FINAL_ACCEPTANCE; the job then waits
# for the independent Human Owner to call ``final_accept()``.
USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"

# The distinct role used by the Human Owner acceptance gate. It is deliberately
# separate from ROLE_CODING_AGENT / ROLE_REVIEWER / scheduler so those actors
# can NEVER impersonate the Human Owner (requirement 6).
ROLE_HUMAN_OWNER = "human_owner"

# The Human Owner acceptance token. It is a SECRET INDEPENDENT from worker
# tokens: it must be supplied via a dedicated channel (never X-Worker-Token),
# is compared only via ``hmac.compare_digest``, and is NEVER stored, logged, or
# embedded in any event payload or error text.
#
# There is intentionally NO module-level default constant here. The former
# ``"change-me-human-owner-token"`` placeholder let production start in an
# insecure state (workers could pass the placeholder through ``final_accept``
# when HERMES_HUMAN_OWNER_TOKEN was unset), so it has been removed. Production
# MUST inject the token explicitly through
# ``ControlPlane(human_owner_token=...)`` — resolved by the production entry
# points from ``HERMES_HUMAN_OWNER_TOKEN``. ``ControlPlane.__init__`` and the
# shared resolver reject the placeholder and any missing/empty/colliding value.

# ---------------------------------------------------------------------------
# PB-23 / D3: Worker-writable event types for the event store (deny-by-default)
# ---------------------------------------------------------------------------
# ``ControlPlane.post_events`` is the ONLY worker/client-facing path that
# accepts caller-supplied event types. It is deny-by-default: ONLY the types
# below — ordinary execution / telemetry events the worker itself generates —
# are accepted. EVERYTHING else is rejected before any row is written, so a
# job-owning worker can never forge an acceptance / completion / authorization
# event. This closes the post_events event-forgery bypass (CVE-class): a worker
# could previously POST a forged ``FINAL_ACCEPTED`` and then call ``complete``
# without any Human-Owner token, defeating P0 (completion-before-acceptance)
# and P1 (independent owner secret).
#
# The allowlist is the EXACT set of event types the production worker
# (``hermes_worker.worker.HermesWorker._run_job``) submits via the
# ``/worker/jobs/{id}/events`` endpoint: ``sandbox_create``, ``execute``,
# ``write_file``. No other event type reaches ``post_events`` — the
# control-plane RESERVED events (FINAL_ACCEPTANCE, FINAL_ACCEPTED,
# TASK_COMPLETED, USER_ACTION_REQUIRED) and all scheduler / release-agent /
# event-router events (commit_created, release_handoff_requested, follow_up,
# round2_label, ci_fail, review, await_user, escalated, push_completed,
# draft_pr_created, ci_pending, delivery_idempotent_reuse, delivery_blocked)
# are written exclusively through the trusted internal ``append_event`` method,
# never through ``post_events``.
WORKER_WRITABLE_EVENTS = frozenset({
    "sandbox_create",
    "execute",
    "write_file",
})
