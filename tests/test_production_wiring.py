"""Focused Phase B production-backend wiring tests (all offline)."""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hermes_worker.agent_runner import RelayAgentRunner
from hermes_worker.docker_sandbox import HermesDockerSandboxBackend
from hermes_worker.echo_sandbox import EchoSandboxBackend
from hermes_worker.protocol import ExecResult
from hermes_worker.reviewer import Reviewer
from hermes_worker.worker import HermesWorker


REPO = "yzhlx/hermes-open-swe-smoke-test"


class FakeDockerSandbox(HermesDockerSandboxBackend):
    """Docker-shaped sandbox double: never calls a Docker daemon."""

    def __init__(self):
        super().__init__(runner=lambda _args: ExecResult(0, "", ""))


class FakeRelay:
    def __init__(self, content="approved"):
        self.config = SimpleNamespace(model="fake-relay-model")
        self.content = content
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.content}}]}

    @staticmethod
    def extract_content(response):
        return response["choices"][0]["message"]["content"]

    @staticmethod
    def extract_usage(_response):
        return {"total_tokens": 3}


class FailingRelay(FakeRelay):
    def complete(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("relay unavailable")


class ProductionWiringTests(unittest.TestCase):
    def test_docker_backend_env_selects_docker_without_daemon(self):
        with patch.dict(os.environ, {"HERMES_SANDBOX_BACKEND": "docker"},
                        clear=False), \
                patch("hermes_worker.worker.HermesDockerSandboxBackend",
                      FakeDockerSandbox):
            worker = HermesWorker("https://cp.example.test", "test-token")
        self.assertIsInstance(worker.backend, FakeDockerSandbox)

    def test_relay_agent_env_selects_runner_and_forces_chat_completions(self):
        relay = FakeRelay("# relay edit\n")
        with patch.dict(os.environ, {"HERMES_AGENT_BACKEND": "relay"},
                        clear=False):
            worker = HermesWorker("https://cp.example.test", "test-token",
                                  relay_client=relay)
        self.assertIsInstance(worker.agent_runner, RelayAgentRunner)
        sandbox = EchoSandboxBackend()
        workspace = sandbox.create()
        evidence = worker.agent_runner.run(sandbox, workspace, "make edit")
        self.assertEqual(evidence.modified_files,
                         ["automation-smoke-test/README.md"])
        self.assertFalse(relay.calls[0]["use_responses"])
        self.assertEqual(sandbox.read_file("automation-smoke-test/README.md"),
                         "# relay edit\n")

    def test_reviewer_llm_approve_is_used_only_when_enabled(self):
        relay = FakeRelay("APPROVE")
        with patch.dict(os.environ, {"HERMES_REVIEWER_LLM": "1"}, clear=False):
            verdict = Reviewer(repo=REPO, relay_client=relay).review(
                {"number": 1}, {"ci_status": "failure"})
        self.assertEqual(verdict.verdict, "APPROVE")
        self.assertFalse(relay.calls[0]["use_responses"])

    def test_reviewer_llm_error_falls_back_to_rules(self):
        relay = FailingRelay()
        with patch.dict(os.environ, {"HERMES_REVIEWER_LLM": "1"}, clear=False):
            verdict = Reviewer(repo=REPO, relay_client=relay).review(
                {"number": 1}, {"ci_status": "failure"})
        self.assertEqual(verdict.verdict, "REQUEST_CHANGES")
        self.assertTrue(verdict.has_blocking)

    def test_env_defaults_remain_echo_and_fake(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = HermesWorker("https://cp.example.test", "test-token")
            reviewer = Reviewer(repo=REPO)
        self.assertIsInstance(worker.backend, EchoSandboxBackend)
        self.assertEqual(type(worker.agent_runner).__name__, "FakeAgentRunner")
        self.assertEqual(reviewer.review({"number": 1}, {"ci_status": "success"}).verdict,
                         "APPROVE")


if __name__ == "__main__":
    unittest.main()
