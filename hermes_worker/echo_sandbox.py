"""Fake sandbox backend for D1 offline protocol testing.

Implements the ``SandboxBackend`` protocol. It uses a REAL temporary directory
so read/write/edit actually round-trip, but ``git_*`` commands are *simulated*
(no real git, no network). This lets us exercise the Open SWE protocol
end-to-end offline, without Docker, GitHub, or any real secret.

It is NOT a real execution environment and must never be used in production.
Phase D2 replaces it with ``HermesDockerSandboxBackend``.
"""
from __future__ import annotations

import os
import time
import tempfile
from typing import Optional

from .protocol import SandboxBackend, ExecResult


class EchoSandboxBackend(SandboxBackend):
    def __init__(self, workspace: Optional[str] = None, timeout: int = 1200):
        self._ws = workspace or tempfile.mkdtemp(prefix="hermes-echo-")
        self._timeout = timeout
        self._created_at = time.time()
        self._alive = True
        self._log = []  # records executed commands for test assertions

    def create(self) -> str:
        os.makedirs(self._ws, exist_ok=True)
        return self._ws

    @property
    def workspace(self) -> str:
        return self._ws

    def execute(self, command, cwd=None, env=None, timeout=1200):
        if not self._alive:
            raise RuntimeError("sandbox destroyed")
        cwd = cwd or self._ws
        self._log.append(command)
        # Echo sandbox: returns the command as stdout, exit 0.
        return ExecResult(exit_code=0, stdout=f"ECHO: {command}\n", stderr="")

    def write_file(self, path, content):
        full = os.path.join(self._ws, path)
        os.makedirs(os.path.dirname(full) or self._ws, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def read_file(self, path):
        full = os.path.join(self._ws, path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def edit_file(self, path, old, new):
        cur = self.read_file(path)
        if old not in cur:
            raise ValueError(f"old text not found in {path}")
        self.write_file(path, cur.replace(old, new, 1))

    def git_clone(self, url, dest):
        self._log.append(f"git clone {url} {dest}")
        return ExecResult(0, f"Cloned {url} into {dest} (simulated)\n", "")

    def git_status(self, repo):
        self._log.append(f"git status in {repo}")
        return ExecResult(0, "On branch main\nnothing to commit (simulated)\n", "")

    def git_diff(self, repo):
        self._log.append(f"git diff in {repo}")
        return ExecResult(0, "diff --git a/x b/x (simulated)\n", "")

    def commit(self, repo, message):
        self._log.append(f"git commit -m {message!r} in {repo}")
        return ExecResult(0, f"[main] {message} (simulated)\n", "")

    def push(self, repo, remote, branch):
        self._log.append(f"git push {remote} {branch} from {repo}")
        return ExecResult(0, f"Pushed {branch} to {remote} (simulated)\n", "")

    def health_check(self):
        return self._alive

    def stop(self):
        self._alive = False

    def delete(self):
        self._alive = False
        # Real backend would `docker rm`. For echo we just invalidate the handle.
        self._ws = None
