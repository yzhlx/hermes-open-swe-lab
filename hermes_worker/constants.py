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
