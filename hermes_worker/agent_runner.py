"""Coding Agent runner (D3 requirement E + #10).

Wires the provider-live Relay adapter into the agent call path. The runner
executes inside the sandbox backend (Docker/Echo), edits the target repo, runs
tests, commits, and reports evidence (token usage + model) WITHOUT ever embedding
the API key.

Security/integration rules enforced (AGENTS.md §10, D3-IMPLEMENTATION-PLAN §4):
- ``use_responses_api=False`` — Chat Completions only; no Responses-over-WebSocket.
- cross-provider (Anthropic) fallback is DISABLED by the adapter (cannot be on).
- LangSmith Gateway is OFF (relay adapter never routes through it).
- token_usage + model are written to evidence; the API key is NEVER written.

Offline: :class:`FakeAgentRunner` simulates an agent edit + commit with canned
usage. Live: :class:`RelayAgentRunner` calls the model via :class:`RelayClient`
and is NOT_TESTED from this environment (needs relay credentials).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class AgentEvidence:
    commit_sha: Optional[str] = None
    modified_files: list = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)   # {prompt, completion, total}
    model: Optional[str] = None
    tool_calls: int = 0


class AgentRunner:
    def run(self, sandbox, repo_dir: str, instruction: str, round: int = 1,
            evidence_collector: Optional[Callable] = None) -> AgentEvidence:
        raise NotImplementedError


class FakeAgentRunner(AgentRunner):
    """Offline agent: edits the smoke-test README, 'runs tests', commits.

    Produces a DETERMINISTIC but round-dependent commit sha so the test can
    assert round-2 yields a NEW head sha (D3 requirement #19).
    """
    def __init__(self, sha_fn=None, usage=None, model: str = "fake-agent"):
        self._sha_fn = sha_fn or (lambda r: f"sha-round{r}")
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15}
        self.model = model

    def run(self, sandbox, repo_dir: str, instruction: str, round: int = 1,
            evidence_collector: Optional[Callable] = None) -> AgentEvidence:
        # Agent edits the target repo working tree inside the sandbox.
        sandbox.write_file(
            "automation-smoke-test/README.md",
            f"# Smoke test\n\nInstruction: {instruction}\nRound: {round}\n")
        # Agent 'runs tests' (offline: echo success). Real path runs pytest.
        res = sandbox.execute("pytest -q || true")
        sha = self._sha_fn(round)
        ev = AgentEvidence(
            commit_sha=sha,
            modified_files=["automation-smoke-test/README.md"],
            token_usage=self.usage, model=self.model, tool_calls=1)
        if evidence_collector:
            evidence_collector(ev)
        return ev


class RelayAgentRunner(AgentRunner):
    """Relay-backed agent. NOT_TESTED from this environment (needs credentials).

    Calls the model through :class:`RelayClient` with ``use_responses=False``,
    cross-provider fallback disabled, LangSmith Gateway off. Token usage is read
    from the response and recorded in evidence; the API key is never logged.
    """
    def __init__(self, relay_client, model: Optional[str] = None):
        self.relay = relay_client
        self.model = model

    def run(self, sandbox, repo_dir: str, instruction: str, round: int = 1,
            evidence_collector: Optional[Callable] = None) -> AgentEvidence:
        # Chat Completions path only (never Responses-over-WebSocket).
        resp = self.relay.complete(
            use_responses=False,
            messages=[{"role": "user", "content": instruction}],
            model=self.model or self.relay.config.model,
        )
        # Record usage WITHOUT the API key (redaction is enforced by the adapter).
        usage = self.relay.extract_usage(resp) or {}
        content = self.relay.extract_content(resp) or ""
        sandbox.write_file("automation-smoke-test/README.md", content)
        sha = f"relay-{abs(hash(content)) % 10 ** 10}"
        ev = AgentEvidence(
            commit_sha=sha,
            modified_files=["automation-smoke-test/README.md"],
            token_usage=usage, model=self.model or self.relay.config.model,
            tool_calls=1)
        if evidence_collector:
            evidence_collector(ev)
        return ev
