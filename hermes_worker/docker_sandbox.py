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

GitHub and coding-Agent credentials are forbidden in the container. Repository
preparation, commit, push, and Draft PR creation are Host Worker operations;
this backend executes target dependency/build/test commands only.

The command runner is injectable (``runner=``) so the backend can be verified
offline without a Docker daemon: tests pass a fake runner that records the
docker argument lists and returns canned ``ExecResult``s.

Real container lifecycle (``docker run`` / ``exec`` / ``rm``) is NOT_TESTED in
unit tests; only argument construction + security flags + file round-trip are
asserted offline.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .protocol import SandboxBackend, ExecResult
from .redact import redact as _redact_text

# --- MVP isolation defaults (per architecture decision) --------------------
DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_CPUS = "1"
DEFAULT_MEMORY = "2g"
DEFAULT_PIDS_LIMIT = "256"
DEFAULT_NETWORK = "bridge"  # isolated bridge, NOT host_network
IDLE_COMMAND = ["tail", "-f", "/dev/null"]  # keeps the container alive for exec

# Env keys whose values must never appear in logs / _calls.
SECRET_ENV_KEYS = {"GITHUB_TOKEN", "GITHUB_APP_TOKEN", "GH_TOKEN"}
FORBIDDEN_CONTAINER_ENV_KEYS = {
    "GITHUB_TOKEN",
    "GITHUB_APP_TOKEN",
    "GH_TOKEN",
    "HERMES_GITHUB_APP_ID",
    "HERMES_GITHUB_INSTALLATION_ID",
    "HERMES_GITHUB_APP_PRIVATE_KEY_PATH",
    "HERMES_GIT_INSTALLATION_TOKEN",
    "CODEX_HOME",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "WEBHOOK_SECRET",
}

# Marker for the only allowed bind mount (task workdir -> /workspace).
WORKSPACE_MOUNT_TARGET = "/workspace"


@dataclass(frozen=True)
class DockerTestResult:
    image_id: str
    container_id: str
    command: str
    exit_code: int
    passed: int
    failed: int
    skipped: int
    timed_out: bool
    stdout_summary: str
    stderr_summary: str
    cleanup_succeeded: bool
    residual_container_count: int


def _summary(text: str, limit: int = 2000) -> str:
    clean = _redact_text(text or "")
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return " | ".join(lines[-12:])[:limit]


def _test_counts(stdout: str, stderr: str, exit_code: int) -> tuple[int, int, int]:
    combined = f"{stdout}\n{stderr}"
    counts = {}
    for label in ("passed", "failed", "skipped"):
        matches = re.findall(rf"(\d+)\s+{label}\b", combined, flags=re.I)
        counts[label] = int(matches[-1]) if matches else 0
    if not any(counts.values()):
        # Generic non-pytest commands still produce truthful binary evidence.
        counts["passed"] = 1 if exit_code == 0 else 0
        counts["failed"] = 0 if exit_code == 0 else 1
    return counts["passed"], counts["failed"], counts["skipped"]


def _redact(args) -> list:
    """Return a copy of ``args`` with any embedded secret replaced by a marker.

    Covers two forms:
    - ``-e KEY=VALUE`` / ``-e KEY`` + ``VALUE`` for known secret env vars
      (``GITHUB_TOKEN`` etc.) — the value is hard-redacted.
    - Any other argument (notably the command string passed to ``bash -lc``,
      e.g. ``git push https://TOKEN@github.com/...``) is passed through the
      generic ``redact()`` so credentials/keys embedded in commands are
      scrubbed too (D3 hardening, item 5).
    """
    out: list = []
    prev = None
    for a in args:
        if prev in ("-e", "--env"):
            if "=" in a:
                k, _, v = a.partition("=")
                if k in SECRET_ENV_KEYS:
                    out.append(f"{k}=***REDACTED***")
                else:
                    out.append(f"{k}={_redact_text(v)}")
            else:
                # value is the next argument (standalone ``-e KEY``)
                out.append(a)
                prev = "__SECRET_VAL__"
                continue
            prev = a
            continue
        if prev == "__SECRET_VAL__":
            out.append("***REDACTED***")
            prev = a
            continue
        # Any other argument (command strings included) is generic-redacted.
        out.append(_redact_text(a))
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
        if github_token:
            raise ValueError("GitHub credentials are forbidden in Docker")
        self._github_token = None
        self._keep_workdir = keep_workdir
        self._cpus = cpus
        self._memory = memory
        self._pids_limit = pids_limit
        self._network = network

        self._ws: Optional[str] = None
        self._container: Optional[str] = None
        self._container_id: Optional[str] = None
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
        if use_token:
            raise RuntimeError("Docker GitHub credential injection is forbidden")
        args = ["docker", "exec"]
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
        self._container_id = res.stdout.strip() or self._container
        self._created_at = time.time()
        return self._ws

    def execute(self, command: str, cwd: Optional[str] = None,
                env: Optional[dict] = None, timeout: int = 1200) -> ExecResult:
        if self._container is None:
            raise RuntimeError("sandbox not created")
        args = ["docker", "exec"]
        for k, v in (env or {}).items():
            if k.upper() in FORBIDDEN_CONTAINER_ENV_KEYS:
                raise ValueError(f"credential environment forbidden in Docker: {k}")
            args += ["-e", f"{k}={v}"]
        args += ["--workdir", cwd or WORKSPACE_MOUNT_TARGET,
                 self._container, "bash", "-lc", command]
        return self._docker(args, timeout=timeout)

    def _safe_path(self, path: str) -> str:
        """Resolve ``path`` against the workspace and reject any escape.

        Prevents ``../`` traversal or absolute paths from writing/reading
        outside the mounted task workdir (host FS escape). This is the
        host-side complement to the single-bind-mount isolation enforced by
        Docker: even if the agent emits a malicious relative path, it cannot
        reach files outside ``self._ws``.
        """
        if not path:
            raise ValueError("empty path")
        full = os.path.normpath(os.path.join(self._ws, path))
        ws = os.path.normpath(self._ws)
        if full != ws and not full.startswith(ws + os.sep):
            raise PermissionError(f"path escapes workspace: {path!r}")
        return full

    def write_file(self, path: str, content: str) -> None:
        full = self._safe_path(path)
        os.makedirs(os.path.dirname(full) or self._ws, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def read_file(self, path: str) -> str:
        with open(self._safe_path(path), "r", encoding="utf-8") as f:
            return f.read()

    def edit_file(self, path: str, old: str, new: str) -> None:
        cur = self.read_file(path)
        if old not in cur:
            raise ValueError(f"old text not found in {path}")
        self.write_file(path, cur.replace(old, new, 1))

    def git_clone(self, url: str, dest: str) -> ExecResult:
        raise RuntimeError("git clone is disabled; Host Worker uses init + fetch")

    def git_status(self, repo: str) -> ExecResult:
        return self._git_exec(["-C", repo, "status"])

    def git_diff(self, repo: str) -> ExecResult:
        return self._git_exec(["-C", repo, "diff"])

    def commit(self, repo: str, message: str) -> ExecResult:
        raise RuntimeError("Docker commit is disabled; Host Worker owns commits")

    def push(self, repo: str, remote: str, branch: str) -> ExecResult:
        raise RuntimeError("Docker push is disabled; Host Worker owns pushes")

    def health_check(self) -> bool:
        if self._container is None:
            return False
        res = self._docker(["docker", "inspect", "-f",
                            "{{.State.Running}}", self._container])
        return res.exit_code == 0 and res.stdout.strip() == "true"

    def run_tests(self, command: str, timeout: int = 1200) -> DockerTestResult:
        """Run the target repository test command in a real Docker container.

        The host worktree is the only bind mount. No GitHub/coding-Agent credential is
        injected. Cleanup and a residual-container query run on every outcome.
        """
        try:
            self.create()
        except Exception as exc:  # noqa: BLE001 - return cleanup evidence
            container_name = self._container
            removal = self._docker(["docker", "rm", "-f", container_name]) \
                if container_name else ExecResult(0, "", "")
            residual_count = 0
            if container_name:
                residual = self._docker([
                    "docker", "ps", "-aq", "--filter",
                    f"name=^/{container_name}$",
                ])
                residual_count = (
                    len([line for line in residual.stdout.splitlines() if line.strip()])
                    if residual.exit_code == 0 else -1
                )
            self._container = None
            self._container_id = None
            if self._ws and os.path.isdir(self._ws) and not self._keep_workdir:
                shutil.rmtree(self._ws, ignore_errors=True)
                self._ws = None
            return DockerTestResult(
                image_id="unknown",
                container_id=container_name or "unknown",
                command=_redact_text(command),
                exit_code=125,
                passed=0,
                failed=1,
                skipped=0,
                timed_out=False,
                stdout_summary="",
                stderr_summary=_summary(str(exc)),
                cleanup_succeeded=(
                    removal.exit_code == 0 and residual_count == 0
                ),
                residual_container_count=residual_count,
            )
        container_name = self._container
        container_id = self._container_id or container_name or "unknown"
        image_id = "unknown"
        image = self._docker(
            ["docker", "image", "inspect", "-f", "{{.Id}}", self._image]
        )
        if image.exit_code == 0 and image.stdout.strip():
            image_id = image.stdout.strip()
        inspected = self._docker(
            ["docker", "inspect", "-f", "{{.Id}}", container_name]
        )
        if inspected.exit_code == 0 and inspected.stdout.strip():
            container_id = inspected.stdout.strip()

        execution = ExecResult(125, "", "docker test did not start")
        cleanup_succeeded = False
        residual_count = -1
        try:
            execution = self.execute(command, timeout=timeout)
        finally:
            removal = self._docker(["docker", "rm", "-f", container_name])
            cleanup_succeeded = removal.exit_code == 0
            residual = self._docker(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"name=^/{container_name}$",
                ]
            )
            if residual.exit_code == 0:
                residual_count = len(
                    [line for line in residual.stdout.splitlines() if line.strip()]
                )
            cleanup_succeeded = (
                removal.exit_code == 0 and residual_count == 0
            )
            self._container = None
            self._container_id = None
            if self._ws and os.path.isdir(self._ws) and not self._keep_workdir:
                shutil.rmtree(self._ws, ignore_errors=True)
                self._ws = None

        passed, failed, skipped = _test_counts(
            execution.stdout, execution.stderr, execution.exit_code
        )
        return DockerTestResult(
            image_id=image_id,
            container_id=container_id,
            command=_redact_text(command),
            exit_code=execution.exit_code,
            passed=passed,
            failed=failed,
            skipped=skipped,
            timed_out=execution.exit_code == 124,
            stdout_summary=_summary(execution.stdout),
            stderr_summary=_summary(execution.stderr),
            cleanup_succeeded=cleanup_succeeded,
            residual_container_count=residual_count,
        )

    def stop(self) -> None:
        if self._container:
            self._docker(["docker", "stop", self._container])

    def delete(self) -> None:
        if self._container:
            self._docker(["docker", "rm", "-f", self._container])
            self._container = None
            self._container_id = None
        if self._ws and os.path.isdir(self._ws) and not self._keep_workdir:
            shutil.rmtree(self._ws, ignore_errors=True)
            self._ws = None

    # -- D3 hook -------------------------------------------------------------
    def set_github_token(self, token: str) -> None:
        del token
        raise RuntimeError(
            "GitHub credentials are host-only and forbidden in Docker"
        )
