"""Host Worker orchestration for Codex edits followed by Docker-only tests."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .codex_cli_runner import CodexCliRunner
from .constants import ALLOWED_GITHUB_REPOS, ROLE_CODING_AGENT
from .control_plane import ControlPlane, ControlPlaneError
from .docker_sandbox import HermesDockerSandboxBackend
from .github_app import GitHubAppTokenBroker, RealAppApiClient
from .github_client import GitHubRestClient
from .redact import redact
from .repository import HostGitOperations, RepositoryPreparer


@dataclass(frozen=True)
class CodexJobResult:
    state: str
    job_id: int
    repo_path: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    error: Optional[str] = None


def _safe_delivery_fragment(delivery_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", delivery_id).strip("-").lower()
    return (value or "delivery")[:12]


class CodexJobRunner:
    """Execute one already-created Job without creating duplicate GitHub objects."""

    def __init__(
        self,
        *,
        control_plane: ControlPlane,
        token_broker,
        codex_runner: CodexCliRunner,
        repository_preparer: RepositoryPreparer,
        git_operations: HostGitOperations,
        docker_backend_factory: Callable,
        github_client,
        work_root: Path,
        keepalive_interval: float = 15.0,
    ):
        self.cp = control_plane
        self.broker = token_broker
        self.codex = codex_runner
        self.preparer = repository_preparer
        self.git = git_operations
        self.docker_backend_factory = docker_backend_factory
        self.github = github_client
        self.work_root = Path(work_root)
        self.keepalive_interval = keepalive_interval

    def _transition(self, job_id: int, worker_token: str,
                    state: str, payload: Optional[dict] = None) -> None:
        self.cp.set_state(job_id, state)
        self.cp.keepalive(worker_token, job_id)
        self.cp.append_event(job_id, {
            "type": state,
            "payload": payload or {},
        })

    def _finish(
        self,
        job_id: int,
        worker_token: str,
        state: str,
        *,
        repo_path: Optional[Path] = None,
        result: Optional[dict] = None,
        error: Optional[str] = None,
        commit_sha: Optional[str] = None,
        pr_number: Optional[int] = None,
        pr_url: Optional[str] = None,
    ) -> CodexJobResult:
        clean_error = redact(error or "") or None
        self.cp.append_event(job_id, {
            "type": state,
            "payload": {
                "error": clean_error,
                "commit_sha": commit_sha,
                "pr_number": pr_number,
            },
        })
        self.cp.finish_state(
            worker_token,
            job_id,
            state,
            result=result or {},
            error=clean_error,
        )
        return CodexJobResult(
            state=state,
            job_id=job_id,
            repo_path=str(repo_path) if repo_path else None,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            error=clean_error,
        )

    def _lease_loop(self, job_id: int, worker_token: str,
                    stop: threading.Event) -> None:
        cp = ControlPlane(self.cp.db_path, lease_seconds=self.cp.lease_seconds)
        try:
            while not stop.wait(self.keepalive_interval):
                try:
                    cp.keepalive(worker_token, job_id)
                except ControlPlaneError:
                    return
        finally:
            cp.conn.close()

    def run(
        self,
        job_id: int,
        worker_token: str,
        repo: str,
        base: str,
        task: str,
        delivery_id: str,
        test_command: str,
        timeout_seconds: int,
    ) -> CodexJobResult:
        if repo not in ALLOWED_GITHUB_REPOS:
            raise ControlPlaneError("repo_not_allowed")
        self.cp.claim_job(worker_token, job_id)
        job = self.cp.get_job(job_id)
        if job.get("repo") and job["repo"] != repo:
            return self._finish(
                job_id, worker_token, "BLOCKED",
                error="job_repository_mismatch",
            )
        payload = json.loads(job.get("payload") or "{}")
        expected_delivery = payload.get("delivery_id")
        if expected_delivery and expected_delivery != delivery_id:
            return self._finish(
                job_id, worker_token, "BLOCKED",
                error="job_delivery_id_mismatch",
            )

        self.work_root.mkdir(parents=True, exist_ok=True)
        task_branch = (
            f"codex/job-{job_id}-{_safe_delivery_fragment(delivery_id)}"
        )
        repo_path = self.work_root / f"job-{job_id}-{uuid.uuid4().hex}"
        stop = threading.Event()
        keepalive = threading.Thread(
            target=self._lease_loop,
            args=(job_id, worker_token, stop),
            daemon=True,
        )
        keepalive.start()

        try:
            self._transition(job_id, worker_token, "REPOSITORY_PREPARING", {
                "repo": repo,
                "base": base,
                "branch": task_branch,
            })
            fetch_token = self.broker.get_token_for_job(
                self.cp, job_id, worker_token
            )
            try:
                prepared = self.preparer.prepare(
                    repo_path, repo, base, task_branch, fetch_token
                )
            finally:
                fetch_token = None
            self.cp.append_event(job_id, {
                "type": "repository_prepared",
                "payload": {
                    "exit_code": prepared.exit_code,
                    "askpass_cleaned": prepared.askpass_cleaned,
                    "token_cleared": prepared.token_cleared,
                    "commands": prepared.commands,
                    "stderr": prepared.stderr_summary,
                },
            })
            if not prepared.ok:
                return self._finish(
                    job_id, worker_token, "BLOCKED",
                    result={"exit_code": prepared.exit_code},
                    error=prepared.stderr_summary or "repository_prepare_failed",
                )

            self._transition(job_id, worker_token, "CODEX_RUNNING")
            codex_result = self.codex.run(
                prepared.repo_path, task, timeout_seconds
            )
            self.cp.append_event(job_id, {
                "type": "codex_result",
                "payload": {
                    "exit_code": codex_result.exit_code,
                    "timed_out": codex_result.timed_out,
                    "duration_seconds": codex_result.duration_seconds,
                    "stdout_summary": codex_result.stdout_summary,
                    "stderr_summary": codex_result.stderr_summary,
                    "changed_files": codex_result.changed_files,
                    "command": codex_result.command_redacted,
                },
            })
            if codex_result.exit_code != 0:
                return self._finish(
                    job_id, worker_token, "CODEX_FAILED",
                    repo_path=prepared.repo_path,
                    result={
                        "exit_code": codex_result.exit_code,
                        "modified_files": codex_result.changed_files,
                        "command": codex_result.command_redacted,
                        "role": ROLE_CODING_AGENT,
                    },
                    error=codex_result.stderr_summary or "codex_failed",
                )

            changed_files = self.git.changed_files(prepared.repo_path)
            self.cp.append_event(job_id, {
                "type": "inspect_changes",
                "payload": {"changed_files": changed_files},
            })
            if not changed_files:
                return self._finish(
                    job_id, worker_token, "CODEX_NO_CHANGES",
                    repo_path=prepared.repo_path,
                    result={
                        "exit_code": 0,
                        "modified_files": [],
                        "command": codex_result.command_redacted,
                        "role": ROLE_CODING_AGENT,
                    },
                )

            self._transition(job_id, worker_token, "TESTING", {
                "command": redact(test_command),
            })
            docker = self.docker_backend_factory(prepared.repo_path)
            test_result = docker.run_tests(
                test_command, timeout=timeout_seconds
            )
            self.cp.append_event(job_id, {
                "type": "docker_test_result",
                "payload": {
                    "image_id": test_result.image_id,
                    "container_id": test_result.container_id,
                    "command": test_result.command,
                    "exit_code": test_result.exit_code,
                    "passed": test_result.passed,
                    "failed": test_result.failed,
                    "skipped": test_result.skipped,
                    "timed_out": test_result.timed_out,
                    "stdout_summary": test_result.stdout_summary,
                    "stderr_summary": test_result.stderr_summary,
                    "cleanup_succeeded": test_result.cleanup_succeeded,
                    "residual_container_count":
                        test_result.residual_container_count,
                },
            })
            if (
                test_result.exit_code != 0
                or test_result.timed_out
                or not test_result.cleanup_succeeded
                or test_result.residual_container_count != 0
            ):
                state = (
                    "TEST_FAILED"
                    if test_result.exit_code != 0 or test_result.timed_out
                    else "BLOCKED"
                )
                return self._finish(
                    job_id, worker_token, state,
                    repo_path=prepared.repo_path,
                    result={
                        "exit_code": test_result.exit_code,
                        "modified_files": changed_files,
                        "container_id": test_result.container_id,
                        "command": test_result.command,
                        "role": ROLE_CODING_AGENT,
                    },
                    error=test_result.stderr_summary or "docker_test_failed",
                )

            self._transition(job_id, worker_token, "COMMITTING")
            committed = self.git.commit(
                prepared.repo_path,
                f"feat: complete Hermes job {job_id}",
            )
            if not committed.created:
                state = "CODEX_NO_CHANGES" if committed.exit_code == 0 else "BLOCKED"
                return self._finish(
                    job_id, worker_token, state,
                    repo_path=prepared.repo_path,
                    result={
                        "exit_code": committed.exit_code,
                        "modified_files": changed_files,
                    },
                    error=committed.stderr_summary or None,
                )

            self._transition(job_id, worker_token, "PUSHING", {
                "branch": task_branch,
            })
            push_token = self.broker.get_token_for_job(
                self.cp, job_id, worker_token
            )
            try:
                pushed = self.git.push(
                    prepared.repo_path, task_branch, push_token
                )
            finally:
                push_token = None
            if pushed.exit_code != 0:
                return self._finish(
                    job_id, worker_token, "BLOCKED",
                    repo_path=prepared.repo_path,
                    result={
                        "exit_code": pushed.exit_code,
                        "modified_files": changed_files,
                        "commit_sha": committed.commit_sha,
                    },
                    error=pushed.stderr or "push_failed",
                    commit_sha=committed.commit_sha,
                )

            pr_token = self.broker.get_token_for_job(
                self.cp, job_id, worker_token
            )
            try:
                pr = self.github.create_draft_pr(
                    repo,
                    task_branch,
                    base,
                    f"Hermes job {job_id}",
                    (
                        f"Automated Codex implementation for job {job_id}. "
                        "Tests ran in HermesDockerSandboxBackend. No merge performed."
                    ),
                    pr_token,
                )
            finally:
                pr_token = None
            if not pr.get("draft", False):
                return self._finish(
                    job_id, worker_token, "BLOCKED",
                    repo_path=prepared.repo_path,
                    error="github_did_not_create_draft_pr",
                    commit_sha=committed.commit_sha,
                )
            return self._finish(
                job_id, worker_token, "PR_CREATED",
                repo_path=prepared.repo_path,
                result={
                    "exit_code": 0,
                    "modified_files": changed_files,
                    "commit_sha": committed.commit_sha,
                    "pr_number": pr["number"],
                    "role": ROLE_CODING_AGENT,
                },
                commit_sha=committed.commit_sha,
                pr_number=pr["number"],
                pr_url=pr.get("url"),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed and release lease
            return self._finish(
                job_id, worker_token, "BLOCKED",
                repo_path=repo_path if repo_path.exists() else None,
                error=redact(str(exc)) or type(exc).__name__,
            )
        finally:
            stop.set()
            keepalive.join(timeout=2)


def _load_worker_token() -> str:
    direct = os.environ.get("HERMES_WORKER_TOKEN", "").strip()
    if direct:
        return direct
    path = os.environ.get("HERMES_WORKER_TOKEN_FILE", "").strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "HERMES_WORKER_TOKEN or HERMES_WORKER_TOKEN_FILE is required"
    )


def _load_broker() -> GitHubAppTokenBroker:
    app_id = os.environ.get("HERMES_GITHUB_APP_ID", "").strip()
    installation_id = os.environ.get(
        "HERMES_GITHUB_INSTALLATION_ID", ""
    ).strip()
    pem_path = os.environ.get(
        "HERMES_GITHUB_APP_PRIVATE_KEY_PATH", ""
    ).strip()
    if not (app_id and installation_id and pem_path):
        raise RuntimeError("GitHub App Token Broker configuration is incomplete")
    resolved_pem = Path(pem_path).resolve()
    if not resolved_pem.is_file():
        raise RuntimeError("GitHub App PEM is unavailable")
    if any((parent / ".git").exists()
           for parent in (resolved_pem.parent, *resolved_pem.parents)):
        raise RuntimeError("GitHub App PEM must be outside every Git repository")
    private_key = resolved_pem.read_text(encoding="utf-8")
    return GitHubAppTokenBroker(
        app_id=app_id,
        installation_id=installation_id,
        private_key_pem=private_key,
        app_api=RealAppApiClient(),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one existing Hermes Job through host Codex, Docker tests, "
            "Host Worker commit/push, and Draft PR creation."
        )
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--job-id", required=True, type=int)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--test-command", required=True)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--db", default=os.environ.get("HERMES_DB_PATH", "runtime/jobs.sqlite"))
    parser.add_argument("--work-root", default=os.environ.get(
        "HERMES_TASK_WORK_ROOT",
        str(Path(tempfile.gettempdir()) / "hermes-task-worktrees"),
    ))
    parser.add_argument("--docker-image", default=os.environ.get(
        "HERMES_DOCKER_IMAGE", "python:3.11-slim"
    ))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo not in ALLOWED_GITHUB_REPOS:
        print(json.dumps({"ok": False, "error": "repo_not_allowed"}))
        return 2
    if args.dry_run:
        plan = {
            "ok": True,
            "dry_run": True,
            "job_id": args.job_id,
            "delivery_id": args.delivery_id,
            "repo": args.repo,
            "base": args.base,
            "codex": {
                "binary_present": bool(shutil.which(args.codex_binary)
                                       or Path(args.codex_binary).is_file()),
                "sandbox": "workspace-write",
                "ephemeral": True,
                "prompt_transport": "stdin",
            },
            "steps": [
                "claim_existing_job",
                "git_init_authenticated_fetch",
                "host_codex_exec",
                "docker_test",
                "host_commit",
                "token_broker_push",
                "draft_pr",
            ],
            "github_writes": False,
        }
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    worker_token = _load_worker_token()
    cp = ControlPlane(args.db)
    broker = _load_broker()
    flow = CodexJobRunner(
        control_plane=cp,
        token_broker=broker,
        codex_runner=CodexCliRunner(args.codex_binary),
        repository_preparer=RepositoryPreparer(),
        git_operations=HostGitOperations(),
        docker_backend_factory=lambda repo_path: HermesDockerSandboxBackend(
            image=args.docker_image,
            workdir=str(repo_path),
            keep_workdir=True,
        ),
        github_client=GitHubRestClient(),
        work_root=Path(args.work_root),
    )
    result = flow.run(
        job_id=args.job_id,
        worker_token=worker_token,
        repo=args.repo,
        base=args.base,
        task=args.task,
        delivery_id=args.delivery_id,
        test_command=args.test_command,
        timeout_seconds=args.timeout,
    )
    print(json.dumps({
        "state": result.state,
        "job_id": result.job_id,
        "repo_path": result.repo_path,
        "commit_sha": result.commit_sha,
        "pr_number": result.pr_number,
        "pr_url": result.pr_url,
        "error": result.error,
    }, ensure_ascii=False))
    return 0 if result.state == "PR_CREATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
