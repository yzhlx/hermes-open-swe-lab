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
# embedded in any event payload or error text. Production MUST set
# HERMES_HUMAN_OWNER_TOKEN; the placeholder below exists only for offline tests.
import os as _os
HUMAN_OWNER_TOKEN = _os.environ.get("HERMES_HUMAN_OWNER_TOKEN",
                                    "change-me-human-owner-token")
