"""Hermes Docker Sandbox Backend (Phase D2).

Real, Docker-based sandbox that conforms to the ``SandboxBackend`` protocol.
The Open SWE agent runs *through* this protocol instead of being bypassed or
replaced by the forbidden Open SWE built-in ``local`` backend.

Architecture (per ADR-002 and the MVP-0 isolation contract):
- One container per task, created with MVP isolation limits and reused for
  follow-up messages (``docker exec`` into the same container).
- On ``delete()`` the container is force-removed (``--rm`` auto-removes it on
  stop too) and the host-side task workdir is wiped.
- ONLY the current task's host workdir is mounted (at ``/workspace``).
  Never mounted: user home, SSH keys, browser cookies, Hermes prod config,
  other GitHub repos, the Docker host socket, or Windows system dirs.
- Isolation defaults: cpus=1, memory=2GB, pids_limit=256, timeout=20min,
  privileged=false, host_network=false, docker_socket_mount=forbidden,
  auto_remove=true, concurrency=1 (enforced at the worker level — one job
  claimed at a time).

Runtime dependency: only the ``docker`` CLI is required (called as a
subprocess). No Docker SDK, no LangSmith, no cloud SDK.

GitHub token (D3 placeholder): a short-lived GitHub App Installation Token
scoped to ``yzhlx/hermes-open-swe-smoke-test`` only may be injected via
``set_github_token()``. It is added as the ``GITHUB_TOKEN`` env var for
``push()`` and is NEVER written to logs (redacted in ``_calls``).

The command runner is injectable (``runner=``) so the backend can be verified
offline without a Docker daemon: tests pass a fake runner that records the
docker argument lists and returns canned ``ExecResult``s.

Real container lifecycle (``docker run`` / ``exec`` / ``rm``) is NOT_TESTED in
unit tests; only argument construction + security flags + file round-trip are
asserted offline.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Optional

from .protocol import SandboxBackend, ExecResult

# --- MVP isolation defaults (per architecture decision) --------------------
DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_CPUS = "1"
DEFAULT_MEMORY = "2g"
DEFAULT_PIDS_LIMIT = "256"
DEFAULT_NETWORK = "bridge"  # isolated bridge, NOT host_network
IDLE_COMMAND = ["tail", "-f", "/dev/null"]  # keeps the container alive for exec

# Env keys whose values must never appear in logs / _calls.
SECRET_ENV_KEYS = {"GITHUB_TOKEN", "GITHUB_APP_TOKEN", "GH_TOKEN"}

# Marker for the only allowed bind mount (task workdir -> /workspace).
WORKSPACE_MOUNT_TARGET = "/workspace"


def _redact(args) -> list:
    """Return a copy of ``args`` with secret env values replaced by a marker.

    Handles both the ``-e KEY=VALUE`` combined form we emit and the rare
    standalone ``-e KEY`` + ``VALUE`` form. The token VALUE itself is never
    stored; only the key name is preserved for debugging.
    """
    out: list = []
    prev = None
    for a in args:
        if prev in ("-e", "--env"):
            if a in SECRET_ENV_KEYS:
                out.append(a)          # keep key name
                prev = "__SECRET_VAL__"
                continue
            if "=" in a and a.split("=", 1)[0] in SECRET_ENV_KEYS:
                k = a.split("=", 1)[0]
                out.append(f"{k}=***REDACTED***")
                prev = a
                continue
            out.append(a)
            prev = a
            continue
        if prev == "__SECRET_VAL__":
            out.append("***REDACTED***")
            prev = a
            continue
        if "=" in a and a.split("=", 1)[0] in SECRET_ENV_KEYS:
            k = a.split("=", 1)[0]
            out.append(f"{k}=***REDACTED***")
            prev = a
            continue
        out.append(a)
        prev = a
    return out


class HermesDockerSandboxBackend(SandboxBackend):
    def __init__(self, image: str = DEFAULT_IMAGE, workdir: Optional[str] = None,
                 runner: Optional[Callable[[list], ExecResult]] = None,
                 github_token: Optional[str] = None, keep_workdir: bool = False,
                 cpus: str = DEFAULT_CPUS, memory: str = DEFAULT_MEMORY,
                 pids_limit: str = DEFAULT_PIDS_LIMIT,
                 network: str = DEFAULT_NETWORK):
        self._image = image
        self._workdir = workdir
        self._runner = runner
        self._github_token = github_token
        self._keep_workdir = keep_workdir
        self._cpus = cpus
        self._memory = memory
        self._pids_limit = pids_limit
        self._network = network

        self._ws: Optional[str] = None
        self._container: Optional[str] = None
        self._created_at: Optional[float] = None
        # Recorded (REDACTED) docker invocations — for inspection / tests.
        self._calls: list = []

    # -- internal helpers ---------------------------------------------------
    def _docker(self, args: list, timeout: Optional[int] = None) -> ExecResult:
        self._calls.append(_redact(args))
        if self._runner is not None:
            return self._runner(list(args))
        try:
            proc = subprocess.run(list(args), capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                124, e.stdout or "",
                (e.stderr or "") + "\n[hermes] command timed out")
        except FileNotFoundError:
            return ExecResult(127, "", "docker CLI not found on PATH")
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def _git_exec(self, git_args: list, use_token: bool = False) -> ExecResult:
        if self._container is None:
            raise RuntimeError("sandbox not created")
        args = ["docker", "exec"]
        if use_token and self._github_token:
            args += ["-e", f"GITHUB_TOKEN={self._github_token}"]
        args += ["--workdir", WORKSPACE_MOUNT_TARGET, self._container, "git"]
        args += git_args
        return self._docker(args)

    # -- SandboxBackend protocol -------------------------------------------
    def create(self) -> str:
        self._ws = self._workdir or tempfile.mkdtemp(prefix="hermes-docker-")
        os.makedirs(self._ws, exist_ok=True)
        self._container = "hermes-" + os.path.basename(self._ws) + \
            "-" + str(int(time.time() * 1000))
        args = [
            "docker", "run", "-d", "--rm",
            "--name", self._container,
            "--cpus", self._cpus,
            "--memory", self._memory,
            "--pids-limit", self._pids_limit,
            "--network", self._network,
            "--workdir", WORKSPACE_MOUNT_TARGET,
            "-v", f"{self._ws}:{WORKSPACE_MOUNT_TARGET}:rw",
            self._image,
        ] + IDLE_COMMAND
        res = self._docker(args)
        if res.exit_code != 0:
            raise RuntimeError(f"docker run failed: {res.stderr}")
        self._created_at = time.time()
        return self._ws

    def execute(self, command: str, cwd: Optional[str] = None,
                env: Optional[dict] = None, timeout: int = 1200) -> ExecResult:
        if self._container is None:
            raise RuntimeError("sandbox not created")
        args = ["docker", "exec"]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args += ["--workdir", cwd or WORKSPACE_MOUNT_TARGET,
                 self._container, "bash", "-lc", command]
        return self._docker(args, timeout=timeout)

    def write_file(self, path: str, content: str) -> None:
        full = os.path.join(self._ws, path)
        os.makedirs(os.path.dirname(full) or self._ws, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def read_file(self, path: str) -> str:
        with open(os.path.join(self._ws, path), "r", encoding="utf-8") as f:
            return f.read()

    def edit_file(self, path: str, old: str, new: str) -> None:
        cur = self.read_file(path)
        if old not in cur:
            raise ValueError(f"old text not found in {path}")
        self.write_file(path, cur.replace(old, new, 1))

    def git_clone(self, url: str, dest: str) -> ExecResult:
        return self._git_exec(["clone", url, dest])

    def git_status(self, repo: str) -> ExecResult:
        return self._git_exec(["-C", repo, "status"])

    def git_diff(self, repo: str) -> ExecResult:
        return self._git_exec(["-C", repo, "diff"])

    def commit(self, repo: str, message: str) -> ExecResult:
        return self._git_exec(["-C", repo, "commit", "-m", message])

    def push(self, repo: str, remote: str, branch: str) -> ExecResult:
        return self._git_exec(["-C", repo, "push", remote, branch],
                              use_token=True)

    def health_check(self) -> bool:
        if self._container is None:
            return False
        res = self._docker(["docker", "inspect", "-f",
                            "{{.State.Running}}", self._container])
        return res.exit_code == 0 and res.stdout.strip() == "true"

    def stop(self) -> None:
        if self._container:
            self._docker(["docker", "stop", self._container])

    def delete(self) -> None:
        if self._container:
            self._docker(["docker", "rm", "-f", self._container])
            self._container = None
        if self._ws and os.path.isdir(self._ws) and not self._keep_workdir:
            shutil.rmtree(self._ws, ignore_errors=True)
            self._ws = None

    # -- D3 hook -------------------------------------------------------------
    def set_github_token(self, token: str) -> None:
        """Inject a short-lived GitHub App Installation Token (D3).

        Scoped to ``yzhlx/hermes-open-swe-smoke-test`` only; added as the
        ``GITHUB_TOKEN`` env var for ``push()``. Never logged (redacted in
        ``_calls``).
        """
        self._github_token = token
