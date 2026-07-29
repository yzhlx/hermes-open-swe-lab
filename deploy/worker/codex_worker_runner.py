"""Dedicated local Host Codex runner for a remote Hermes ControlPlane."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from typing import Mapping, Optional

from hermes_worker.codex_cli_runner import CodexCliRunner
from hermes_worker.codex_job_runner import CodexJobRunner
from hermes_worker.constants import ALLOWED_GITHUB_REPOS
from hermes_worker.control_plane_http_client import ControlPlaneHttpClient
from hermes_worker.docker_sandbox import HermesDockerSandboxBackend
from hermes_worker.github_client import GitHubRestClient
from hermes_worker.remote_token_broker import RemoteTokenBroker
from hermes_worker.repository import HostGitOperations, RepositoryPreparer

from .worker_runner import (
    acquire_lock,
    docker_preflight,
    load_token,
    release_lock,
)


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

    token_file = Path(
        env.get(
            "HERMES_WORKER_TOKEN_FILE",
            str(Path.home() / ".hermes" / "worker_token"),
        )
    )
    return {
        "cloud_url": cloud_url,
        "localhost_test": localhost_test,
        "token_file": token_file,
        "codex_binary": env.get("HERMES_CODEX_BINARY", "codex"),
        "docker_image": env.get(
            "HERMES_DOCKER_IMAGE",
            "python:3.11-slim",
        ),
        "work_root": Path(env.get(
            "HERMES_TASK_WORK_ROOT",
            str(Path(os.environ.get("TEMP", ".")) / "hermes-task-worktrees"),
        )),
        "lock_path": env.get(
            "HERMES_CODEX_WORKER_LOCK",
            str(Path.home() / ".hermes" / "codex-worker.lock"),
        ),
        "http_timeout": float(env.get("HERMES_CLOUD_TIMEOUT", "30")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one remote Hermes job through the dedicated Host Codex flow"
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
        "runner": "CodexJobRunner",
        "sandbox": "HermesDockerSandboxBackend",
        "codex": {
            "workspace_write": True,
            "ephemeral": True,
            "prompt_transport": "stdin",
            "binary_present": bool(
                shutil.which(config["codex_binary"])
                or Path(config["codex_binary"]).is_file()
            ),
        },
        "steps": [
            "remote_register",
            "claim_exact_job",
            "authenticated_shallow_fetch",
            "host_codex_workspace_write",
            "docker_tests",
            "host_commit",
            "lease_gated_token",
            "host_push",
            "draft_pr",
        ],
        "github_writes": False,
        "cloud_writes": False,
    }


def codex_preflight(binary: str) -> None:
    if not (shutil.which(binary) or Path(binary).is_file()):
        raise RuntimeError("codex_binary_unavailable")


def run_job(config: dict, args: argparse.Namespace):
    if args.repo not in ALLOWED_GITHUB_REPOS:
        raise ValueError("repo_not_allowed")
    if args.job_id <= 0:
        raise ValueError("invalid_job_id")

    docker_preflight()
    codex_preflight(config["codex_binary"])
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
        runner = CodexJobRunner(
            control_plane=client,
            token_broker=broker,
            codex_runner=CodexCliRunner(config["codex_binary"]),
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
