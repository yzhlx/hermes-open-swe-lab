"""Shared fixtures/helpers for deployment tests (offline / safe environment)."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[2]


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def bash_path() -> str | None:
    return shutil.which("bash")


def run_bash(script: str, env: dict | None = None) -> tuple[int, str]:
    """Run a bash script with an environment; returns (rc, combined_output)."""
    bp = bash_path()
    if bp is None:
        pytest.skip("bash not available")
    full = dict(os.environ)
    if env:
        full.update(env)
    p = subprocess.run([bp, script], cwd=str(WORKTREE), env=full,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def start_control_plane(port: int, db_path: str, localhost_test: bool = True,
                        extra: dict | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(WORKTREE)
    env["HERMES_DB_PATH"] = db_path
    env["HERMES_LISTEN_HOST"] = "127.0.0.1"
    env["HERMES_LISTEN_PORT"] = str(port)
    env["HERMES_LOCALHOST_TEST"] = "1" if localhost_test else "0"
    env["HERMES_RUNTIME_DIR"] = os.path.dirname(db_path) or "."
    env["HERMES_LOG_DIR"] = os.path.dirname(db_path) or "."
    # The production Control Plane requires an explicit, non-empty
    # ALLOWED_WORKER_TOKENS (fail-closed). Deployment tests boot the REAL
    # entry point, so they must supply a valid allowlist that matches the
    # worker token used by the test. Overridable via ``extra``.
    env["ALLOWED_WORKER_TOKENS"] = "test-worker-token"
    # The production Control Plane also requires an explicit, non-empty,
    # non-placeholder Human-Owner token (fail-closed P1 remediation). It must
    # not equal the worker token. Deployment tests boot the REAL entry point,
    # so they must supply it or the process exits(2) before binding.
    env["HERMES_HUMAN_OWNER_TOKEN"] = "test-human-owner-token"
    if extra:
        env.update(extra)
    p = subprocess.Popen(
        [sys.executable, "-m", "deploy.cloud.control_plane_app"],
        cwd=str(WORKTREE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p


def wait_for_health(port: int, timeout: float = 15.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                        timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def build_git_repo(tmp_path: Path) -> Path:
    """Copy the worktree into a temp git repo (excluding VCS/caches)."""
    import tempfile
    repo = tmp_path / "src"
    repo.mkdir()
    shutil.copytree(WORKTREE, repo, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        ".git", ".workbuddy", "__pycache__", "*.pyc",
                        ".venv", "*.egg-info"))
    # remove any nested .git just in case
    for g in repo.rglob(".git"):
        if g.is_dir():
            shutil.rmtree(g, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@hermes.local"],
                   cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo),
                   check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo),
                   check=True)
    return repo


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message],
                   cwd=str(repo), check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()
