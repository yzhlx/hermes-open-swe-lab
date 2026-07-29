"""Dedicated local Host Pi Agent runner for a remote Hermes ControlPlane."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, Optional

from hermes_worker.constants import ALLOWED_GITHUB_REPOS
from hermes_worker.control_plane_http_client import ControlPlaneHttpClient
from hermes_worker.docker_sandbox import HermesDockerSandboxBackend
from hermes_worker.github_client import GitHubRestClient
from hermes_worker.host_agent_job_runner import HostAgentJobRunner
from hermes_worker.pi_cli_runner import PiCliRunner
from hermes_worker.remote_token_broker import RemoteTokenBroker
from hermes_worker.repository import HostGitOperations, RepositoryPreparer

from .worker_runner import (
    acquire_lock,
    docker_preflight,
    load_token,
    release_lock,
)


_PINNED_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_DEFAULT_DOCKER_IMAGE = (
    "python:3.11-slim@sha256:"
    "db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93"
)
_VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"{key.lower()}_required")
    return value


def load_config(env: Mapping[str, str]) -> dict:
    cloud_url = env.get("HERMES_CLOUD_URL", "").rstrip("/")
    localhost_test = env.get("HERMES_LOCALHOST_TEST", "0") == "1"
    if cloud_url.startswith("http://"):
        if not (
            localhost_test
            and (
                cloud_url.startswith("http://127.0.0.1")
                or cloud_url.startswith("http://localhost")
                or cloud_url.startswith("http://[::1]")
            )
        ):
            raise ValueError("https_required")
    elif not cloud_url.startswith("https://"):
        raise ValueError("https_required")

    provider = _required(env, "HERMES_PI_PROVIDER")
    model = _required(env, "HERMES_PI_MODEL")
    thinking = _required(env, "HERMES_PI_THINKING")
    if thinking not in _VALID_THINKING:
        raise ValueError("invalid_pi_thinking")

    agent_dir = Path(_required(env, "HERMES_PI_AGENT_DIR"))
    if not agent_dir.is_absolute():
        raise ValueError("pi_agent_dir_must_be_absolute")

    extension_default = Path(__file__).with_name("pi_workspace_extension.ts")
    extension_path = Path(env.get(
        "HERMES_PI_EXTENSION_PATH",
        str(extension_default),
    ))
    if not extension_path.is_absolute():
        extension_path = extension_path.resolve()

    docker_image = env.get("HERMES_DOCKER_IMAGE", _DEFAULT_DOCKER_IMAGE)
    if not _PINNED_IMAGE_RE.fullmatch(docker_image):
        raise ValueError("docker_image_digest_required")

    token_file = Path(env.get(
        "HERMES_WORKER_TOKEN_FILE",
        str(Path.home() / ".hermes" / "worker_token"),
    ))
    return {
        "cloud_url": cloud_url,
        "localhost_test": localhost_test,
        "token_file": token_file,
        "pi_binary": env.get("HERMES_PI_BINARY", "pi"),
        "pi_provider": provider,
        "pi_model": model,
        "pi_thinking": thinking,
        "pi_agent_dir": agent_dir,
        "pi_extension_path": extension_path,
        "pi_artifacts_root": Path(env.get(
            "HERMES_PI_ARTIFACTS_ROOT",
            str(Path(os.environ.get("TEMP", ".")) / "hermes-pi-diagnostics"),
        )),
        "docker_image": docker_image,
        "work_root": Path(env.get(
            "HERMES_TASK_WORK_ROOT",
            str(Path(os.environ.get("TEMP", ".")) / "hermes-task-worktrees"),
        )),
        "lock_path": env.get(
            "HERMES_PI_WORKER_LOCK",
            str(Path.home() / ".hermes" / "pi-worker.lock"),
        ),
        "http_timeout": float(env.get("HERMES_CLOUD_TIMEOUT", "30")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one remote Hermes job through the dedicated Host Pi Agent flow"
    )
    parser.add_argument("--job-id", required=True, type=int)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--test-command", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def dry_run_plan(
    config: dict,
    *,
    job_id: int,
    repo: str,
    base: str,
    delivery_id: str,
) -> dict:
    if repo not in ALLOWED_GITHUB_REPOS:
        raise ValueError("repo_not_allowed")
    return {
        "ok": True,
        "dry_run": True,
        "job_id": int(job_id),
        "repo": repo,
        "base": base,
        "delivery_id": delivery_id,
        "runner": "HostAgentJobRunner",
        "agent": "PiCliRunner",
        "pi": {
            "binary_present": bool(
                shutil.which(config["pi_binary"])
                or Path(config["pi_binary"]).is_file()
            ),
            "provider": config["pi_provider"],
            "model": config["pi_model"],
            "thinking": config["pi_thinking"],
            "agent_dir_present": config["pi_agent_dir"].is_dir(),
            "extension_present": config["pi_extension_path"].is_file(),
            "prompt_transport": "stdin",
            "session": False,
            "project_resources": False,
            "built_in_tools": False,
        },
        "tools": [
            "read", "write", "edit", "ls", "find", "grep", "submit_result"
        ],
        "steps": [
            "remote_register",
            "claim_exact_job",
            "authenticated_shallow_fetch",
            "host_pi_contained_workspace_edit",
            "docker_tests",
            "host_commit",
            "lease_gated_token",
            "host_push",
            "draft_pr",
        ],
        "sandbox": "HermesDockerSandboxBackend",
        "docker_image": config["docker_image"],
        "github_writes": False,
        "cloud_writes": False,
        "provider_calls": False,
    }


def pi_preflight(config: dict) -> str:
    resolved = PiCliRunner.resolve_binary(config["pi_binary"])
    if not config["pi_agent_dir"].is_dir():
        raise RuntimeError("pi_agent_dir_unavailable")
    if not config["pi_extension_path"].is_file():
        raise RuntimeError("pi_extension_unavailable")
    guard = config["pi_extension_path"].with_name("pi_workspace_guard.mjs")
    if not guard.is_file():
        raise RuntimeError("pi_workspace_guard_unavailable")
    return resolved


def run_job(config: dict, args: argparse.Namespace):
    if args.repo not in ALLOWED_GITHUB_REPOS:
        raise ValueError("repo_not_allowed")
    if args.job_id <= 0:
        raise ValueError("invalid_job_id")

    docker_preflight()
    resolved_pi = pi_preflight(config)
    worker_token = load_token(str(config["token_file"]), strict=True)
    lock_path = config["lock_path"]
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    acquire_lock(lock_path)
    try:
        client = ControlPlaneHttpClient(
            config["cloud_url"],
            worker_token,
            timeout=config["http_timeout"],
            insecure_local_ok=config["localhost_test"],
        )
        client.register()
        broker = RemoteTokenBroker(client)
        runner = HostAgentJobRunner(
            control_plane=client,
            token_broker=broker,
            agent_runner=PiCliRunner(
                resolved_pi,
                provider=config["pi_provider"],
                model=config["pi_model"],
                thinking=config["pi_thinking"],
                agent_dir=config["pi_agent_dir"],
                extension_path=config["pi_extension_path"],
                artifacts_root=config["pi_artifacts_root"],
            ),
            repository_preparer=RepositoryPreparer(),
            git_operations=HostGitOperations(),
            docker_backend_factory=lambda repo_path: HermesDockerSandboxBackend(
                image=config["docker_image"],
                workdir=str(repo_path),
                keep_workdir=True,
            ),
            github_client=GitHubRestClient(repo=args.repo),
            work_root=config["work_root"],
        )
        return runner.run(
            job_id=args.job_id,
            worker_token=worker_token,
            repo=args.repo,
            base=args.base,
            task=args.task,
            delivery_id=args.delivery_id,
            test_command=args.test_command,
            timeout_seconds=args.timeout,
        )
    finally:
        worker_token = None
        release_lock(lock_path)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(os.environ)
        if args.dry_run:
            print(json.dumps(
                dry_run_plan(
                    config,
                    job_id=args.job_id,
                    repo=args.repo,
                    base=args.base,
                    delivery_id=args.delivery_id,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ))
            return 0
        result = run_job(config, args)
        print(json.dumps({
            "state": result.state,
            "job_id": result.job_id,
            "commit_sha": result.commit_sha,
            "pr_number": result.pr_number,
            "pr_url": result.pr_url,
            "error": result.error,
        }, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.state in {"PR_CREATED", "PR_UPDATED"} else 1
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": type(exc).__name__,
        }, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
