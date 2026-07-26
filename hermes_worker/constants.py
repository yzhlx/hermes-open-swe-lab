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
