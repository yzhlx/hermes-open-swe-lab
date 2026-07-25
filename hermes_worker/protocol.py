"""Sandbox backend protocol for Open SWE.

Defines the ``SandboxBackend`` interface that any sandbox implementation
(``EchoSandboxBackend`` for D1 offline tests, ``HermesDockerSandboxBackend``
for D2) must satisfy, so the Open SWE agent runs *through* the protocol instead
of being bypassed or replaced by the forbidden Open SWE local backend.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


class JobState:
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STATES = (PENDING, CLAIMED, RUNNING, COMPLETED, FAILED)


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class SandboxBackend(abc.ABC):
    """Conforming implementation runs Open SWE agent steps in isolation."""

    @abc.abstractmethod
    def create(self) -> str:
        """Create a sandbox workspace; return a stable workspace id."""

    @abc.abstractmethod
    def execute(self, command: str, cwd: Optional[str] = None,
                env: Optional[dict] = None, timeout: int = 1200) -> ExecResult:
        """Run a command; return exit code + captured output."""

    @abc.abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a file inside the workspace."""

    @abc.abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file inside the workspace."""

    @abc.abstractmethod
    def edit_file(self, path: str, old: str, new: str) -> None:
        """Replace ``old`` with ``new`` in a file (raises if not found)."""

    @abc.abstractmethod
    def git_clone(self, url: str, dest: str) -> ExecResult:
        """Clone a repo into the workspace."""

    @abc.abstractmethod
    def git_status(self, repo: str) -> ExecResult:
        """git status in repo."""

    @abc.abstractmethod
    def git_diff(self, repo: str) -> ExecResult:
        """git diff in repo."""

    @abc.abstractmethod
    def commit(self, repo: str, message: str) -> ExecResult:
        """commit in repo."""

    @abc.abstractmethod
    def push(self, repo: str, remote: str, branch: str) -> ExecResult:
        """push branch to remote."""

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Return True if the sandbox is healthy."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop the sandbox but keep the workspace for reuse."""

    @abc.abstractmethod
    def delete(self) -> None:
        """Destroy the sandbox and its workspace."""


# Minimal observability record emitted per task step. Never contains secrets.
@dataclass
class TaskEvent:
    event_type: str
    payload: dict
    event_id: Optional[str] = None
