from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from hermes_worker.pi_cli_runner import AgentRunResult
from hermes_worker.host_agent_job_runner import HostAgentJobRunner
from hermes_worker.control_plane import ControlPlane
from hermes_worker.db import hash_token
from hermes_worker.docker_sandbox import DockerTestResult, HermesDockerSandboxBackend
from hermes_worker.repository import (
    CommandResult,
    CommitResult,
    RepositoryPrepareResult,
    RepositoryPreparer,
    HostGitOperations,
    _is_transient_git_failure,
)


REPO = "yzhlx/hermes-open-swe-smoke-test"
FAKE_TOKEN = "ghs_FAKE_INSTALLATION_TOKEN_123456"


class FakeGitCommandRunner:
    def __init__(self, fetch_exit=0, fetch_stderr=""):
        self.calls = []
        self.fetch_exit = fetch_exit
        self.fetch_stderr = fetch_stderr

    def __call__(self, args, *, cwd, env, timeout):
        args = list(args)
        self.calls.append((args, Path(cwd), dict(env)))
        if args[1:] == ["init"]:
            (Path(cwd) / ".git" / "info").mkdir(parents=True, exist_ok=True)
        if "fetch" in args:
            return CommandResult(self.fetch_exit, "", self.fetch_stderr)
        return CommandResult(0, "", "")


class RepositoryPreparerTests(unittest.TestCase):
    def test_git_init_authenticated_fetch_replaces_clone_and_cleans_askpass(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "task-repo"
            fake = FakeGitCommandRunner()
            preparer = RepositoryPreparer(
                git_binary="git-test", command_runner=fake,
                environ={"PATH": os.environ.get("PATH", "")},
            )
            result = preparer.prepare(
                destination, REPO, "main", "pi/job-9", FAKE_TOKEN
            )
            self.assertTrue(result.ok)
            flat_args = [" ".join(call[0]) for call in fake.calls]
            self.assertTrue(any(command.endswith(" init") for command in flat_args))
            self.assertTrue(any(" fetch --depth 1 origin main" in command
                                for command in flat_args))
            self.assertTrue(any(" checkout -b pi/job-9 FETCH_HEAD" in command
                                for command in flat_args))
            self.assertFalse(any(" clone " in f" {command} " for command in flat_args))
            fetch = next(call for call in fake.calls if "fetch" in call[0])
            self.assertEqual(fetch[2]["HERMES_GIT_INSTALLATION_TOKEN"], FAKE_TOKEN)
            self.assertEqual(fetch[2]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(fetch[2]["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertFalse(Path(fetch[2]["GIT_ASKPASS"]).exists())
            serialized = json.dumps(result.commands) + result.stderr_summary
            self.assertNotIn(FAKE_TOKEN, serialized)

    def test_fetch_retries_transient_network_errors_with_same_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "task-repo"
            calls = []
            outcomes = iter([
                CommandResult(128, "", "OpenSSL SSL_read: Connection was reset"),
                CommandResult(128, "", "Failed to connect: timed out"),
                CommandResult(0, "", ""),
            ])

            def runner(args, *, cwd, env, timeout):
                args = list(args)
                calls.append((args, dict(env)))
                if args[1:] == ["init"]:
                    (Path(cwd) / ".git" / "info").mkdir(parents=True, exist_ok=True)
                if "fetch" in args:
                    return next(outcomes)
                return CommandResult(0, "", "")

            result = RepositoryPreparer(
                command_runner=runner,
                sleeper=lambda _seconds: None,
                environ={"PATH": os.environ.get("PATH", "")},
            ).prepare(destination, REPO, "main", "pi/job-9", FAKE_TOKEN)

            self.assertTrue(result.ok)
            fetches = [call for call in calls if "fetch" in call[0]]
            self.assertEqual(len(fetches), 3)
            self.assertTrue(all(
                "http.version=HTTP/1.1" in call[0] for call in fetches
            ))
            self.assertTrue(all(
                call[1]["HERMES_GIT_INSTALLATION_TOKEN"] == FAKE_TOKEN
                for call in fetches
            ))

    def test_push_retries_transient_network_error_with_same_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "task-repo"
            repo.mkdir()
            calls = []
            outcomes = iter([
                CommandResult(128, "", "RPC failed: connection was reset"),
                CommandResult(0, "", ""),
            ])

            def runner(args, *, cwd, env, timeout):
                calls.append((list(args), dict(env)))
                return next(outcomes)

            result = HostGitOperations(
                command_runner=runner,
                sleeper=lambda _seconds: None,
                environ={"PATH": os.environ.get("PATH", "")},
            ).push(repo, "pi/job-9", FAKE_TOKEN)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(
                "http.version=HTTP/1.1" in call[0] for call in calls
            ))
            self.assertTrue(all(
                call[1]["HERMES_GIT_INSTALLATION_TOKEN"] == FAKE_TOKEN
                for call in calls
            ))

    def test_fetch_does_not_retry_http_403_even_with_rpc_failed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "task-repo"
            calls = []

            def runner(args, *, cwd, env, timeout):
                args = list(args)
                if args[1:] == ["init"]:
                    (Path(cwd) / ".git" / "info").mkdir(parents=True, exist_ok=True)
                if "fetch" in args:
                    calls.append(args)
                    return CommandResult(
                        128,
                        "",
                        "The requested URL returned error: 403; connection was reset",
                    )
                return CommandResult(0, "", "")

            result = RepositoryPreparer(
                command_runner=runner,
                sleeper=lambda _seconds: None,
                environ={"PATH": os.environ.get("PATH", "")},
            ).prepare(destination, REPO, "main", "pi/job-9", FAKE_TOKEN)

            self.assertFalse(result.ok)
            self.assertEqual(len(calls), 1)

    def test_git_failure_classifier_handles_common_curl_permission_and_ref_formats(self):
        permanent = (
            "HTTP 403 Forbidden; connection was reset",
            "The requested URL returned error: 403; connection was reset",
            "Permission to org/repo.git denied to user; remote end hung up",
            "src refspec missing does not match any; connection was reset",
            "rejected (non-fast-forward); remote end hung up",
        )
        transient = (
            "The requested URL returned error: 502",
            "HTTP/2 stream was reset",
            "OpenSSL SSL_read: connection was reset",
            "OpenSSL SSL_connect: SSL_ERROR_SYSCALL",
            "GnuTLS recv error (-110): The TLS connection was non-properly terminated",
        )
        for message in permanent:
            self.assertFalse(_is_transient_git_failure(
                CommandResult(128, "", message)
            ))
        for message in transient:
            self.assertTrue(_is_transient_git_failure(
                CommandResult(128, "", message)
            ))

    def test_push_does_not_retry_non_fast_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "task-repo"
            repo.mkdir()
            calls = []

            def runner(args, *, cwd, env, timeout):
                calls.append(list(args))
                return CommandResult(
                    1,
                    "",
                    "rejected (non-fast-forward); remote end hung up",
                )

            result = HostGitOperations(
                command_runner=runner,
                sleeper=lambda _seconds: None,
                environ={"PATH": os.environ.get("PATH", "")},
            ).push(repo, "pi/job-9", FAKE_TOKEN)

            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(len(calls), 1)

    def test_push_transient_failures_stop_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "task-repo"
            repo.mkdir()
            calls = []

            def runner(args, *, cwd, env, timeout):
                calls.append(list(args))
                return CommandResult(128, "", "connection was reset")

            result = HostGitOperations(
                command_runner=runner,
                sleeper=lambda _seconds: None,
                environ={"PATH": os.environ.get("PATH", "")},
            ).push(repo, "pi/job-9", FAKE_TOKEN)

            self.assertEqual(result.exit_code, 128)
            self.assertEqual(len(calls), 3)

    def test_changed_files_expands_untracked_directories_to_exact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "task-repo"
            repo.mkdir()
            calls = []

            def runner(args, *, cwd, env, timeout):
                args = list(args)
                calls.append(args)
                output = (
                    "?? automation-smoke-test/README.md\0"
                    if "--untracked-files=all" in args
                    else "?? automation-smoke-test/\0"
                )
                return CommandResult(0, output, "")

            files = HostGitOperations(command_runner=runner).changed_files(repo)

            self.assertEqual(files, ["automation-smoke-test/README.md"])
            self.assertIn("--untracked-files=all", calls[0])

    def test_fetch_failure_is_redacted_and_directory_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "task-repo"
            fake = FakeGitCommandRunner(
                fetch_exit=128, fetch_stderr=f"token={FAKE_TOKEN}"
            )
            result = RepositoryPreparer(
                command_runner=fake,
                environ={"PATH": os.environ.get("PATH", "")},
            ).prepare(destination, REPO, "main", "pi/job-9", FAKE_TOKEN)
            self.assertFalse(result.ok)
            self.assertEqual(result.exit_code, 128)
            self.assertNotIn(FAKE_TOKEN, result.stderr_summary)
            self.assertFalse(destination.exists())
            self.assertTrue(result.cleaned_on_failure)


class DockerTestEvidenceTests(unittest.TestCase):
    def test_run_tests_records_runtime_and_cleanup_evidence(self):
        calls = []

        def docker(args):
            calls.append(list(args))
            if args[:3] == ["docker", "run", "-d"]:
                return CommandResult(0, "container-created\n", "")
            if args[:3] == ["docker", "image", "inspect"]:
                return CommandResult(0, "sha256:image\n", "")
            if args[:2] == ["docker", "inspect"]:
                return CommandResult(0, "sha256:container\n", "")
            if args[:2] == ["docker", "exec"]:
                return CommandResult(0, "2 passed, 1 skipped\n", "")
            return CommandResult(0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            backend = HermesDockerSandboxBackend(
                workdir=tmp,
                keep_workdir=True,
                runner=docker,
            )
            result = backend.run_tests("pytest -q", timeout=30)
        self.assertEqual(result.image_id, "sha256:image")
        self.assertEqual(result.container_id, "sha256:container")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((result.passed, result.failed, result.skipped), (2, 0, 1))
        self.assertTrue(result.cleanup_succeeded)
        self.assertEqual(result.residual_container_count, 0)
        self.assertTrue(any(call[:3] == ["docker", "rm", "-f"] for call in calls))
        self.assertTrue(any(call[:3] == ["docker", "ps", "-aq"] for call in calls))


class FakeBroker:
    def __init__(self):
        self.calls = 0

    def get_token_for_job(self, cp, job_id, worker_token):
        self.calls += 1
        return FAKE_TOKEN


class FakePreparer:
    def __init__(self, root):
        self.root = Path(root)
        self.calls = 0

    def prepare(self, destination, repo, base, branch, token):
        self.calls += 1
        destination = Path(destination)
        destination.mkdir(parents=True)
        return RepositoryPrepareResult(
            ok=True, repo_path=destination, exit_code=0,
            stderr_summary="", commands=["git init", "git fetch", "git checkout"],
            askpass_cleaned=True, token_cleared=True, cleaned_on_failure=False,
        )


class FakeAgent:
    def __init__(self, exit_code=0, changed_files=None):
        self.exit_code = exit_code
        self.changed_files = ["feature.py"] if changed_files is None else changed_files
        self.calls = 0

    def run(self, repo_path, prompt, timeout_seconds):
        self.calls += 1
        return AgentRunResult(
            exit_code=self.exit_code,
            timed_out=False,
            duration_seconds=0.1,
            events_jsonl_path=str(Path(repo_path) / ".hermes" / "events.jsonl"),
            final_message_path=str(Path(repo_path) / ".hermes" / "final.txt"),
            stdout_summary="events=1",
            stderr_summary="" if self.exit_code == 0 else "agent failed",
            changed_files=list(self.changed_files),
            command_redacted="agent exec --sandbox workspace-write ...",
        )


class FakeDockerBackend:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.calls = 0

    def run_tests(self, command, timeout=1200):
        self.calls += 1
        return DockerTestResult(
            image_id="sha256:image",
            container_id="container-id",
            command=command,
            exit_code=self.exit_code,
            passed=1 if self.exit_code == 0 else 0,
            failed=0 if self.exit_code == 0 else 1,
            skipped=0,
            timed_out=False,
            stdout_summary="1 passed" if self.exit_code == 0 else "",
            stderr_summary="" if self.exit_code == 0 else "1 failed",
            cleanup_succeeded=True,
            residual_container_count=0,
        )


class FakeGitOperations:
    def __init__(self, changed_files=None):
        self.files = ["feature.py"] if changed_files is None else changed_files
        self.commit_calls = 0
        self.push_calls = 0

    def changed_files(self, repo_path):
        return list(self.files)

    def commit(self, repo_path, message):
        self.commit_calls += 1
        return CommitResult(True, "a" * 40, 0, "")

    def push(self, repo_path, branch, token):
        self.push_calls += 1
        return CommandResult(0, "", "")


class FakeGitHub:
    def __init__(self):
        self.pr_calls = 0

    def create_draft_pr(self, repo, branch, base, title, body, token):
        self.pr_calls += 1
        return {"number": 12, "url": "https://example.invalid/pr/12", "draft": True}


class HostAgentJobRunnerStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = "worker-secret"
        self.cp = ControlPlane(
            str(self.root / "jobs.sqlite"),
            allowed_token_hashes={hash_token(self.token)},
        )
        self.cp.register(self.token, "test-worker")

    def tearDown(self):
        self.cp.conn.close()
        self.tmp.cleanup()

    def create_job(self):
        return self.cp.create_job(
            {"delivery_id": "delivery-1"},
            task_id="task-1", repo=REPO, role="coding_agent",
        )

    def build_flow(self, *, agent_exit=0, changed_files=None, test_exit=0):
        agent = FakeAgent(agent_exit, changed_files)
        docker = FakeDockerBackend(test_exit)
        git_ops = FakeGitOperations(changed_files)
        github = FakeGitHub()
        flow = HostAgentJobRunner(
            control_plane=self.cp,
            token_broker=FakeBroker(),
            agent_runner=agent,
            repository_preparer=FakePreparer(self.root),
            git_operations=git_ops,
            docker_backend_factory=lambda repo_path: docker,
            github_client=github,
            work_root=self.root / "work",
        )
        return flow, agent, docker, git_ops, github

    def test_success_runs_agent_then_docker_commit_push_and_draft_pr(self):
        job_id = self.create_job()
        flow, agent, docker, git_ops, github = self.build_flow()
        result = flow.run(
            job_id=job_id, worker_token=self.token, repo=REPO, base="main",
            task="implement", delivery_id="delivery-1",
            test_command="pytest -q", timeout_seconds=60,
        )
        self.assertEqual(result.state, "PR_CREATED")
        self.assertEqual(agent.calls, 1)
        self.assertEqual(docker.calls, 1)
        self.assertEqual(git_ops.commit_calls, 1)
        self.assertEqual(git_ops.push_calls, 1)
        self.assertEqual(github.pr_calls, 1)
        self.assertEqual(flow.broker.calls, 3)
        event_types = [event["event_type"] for event in self.cp.get_events(job_id)]
        self.assertLess(event_types.index("AGENT_RUNNING"),
                        event_types.index("TESTING"))
        self.assertIn("pr_created", event_types)
        job = self.cp.get_job(job_id)
        # PR creation completes only the Host Worker delivery phase.  The
        # product task remains reviewable/reclaimable for round-2.
        self.assertEqual(job["state"], "agent_done")
        self.assertIsNone(job["ended_at"])
        self.assertEqual(job["worker_token_hash"], hash_token(self.token))
        self.assertIsNone(job["lease_expires"])
        evidence = json.dumps({
            "job": job,
            "events": self.cp.get_events(job_id),
        })
        self.assertNotIn(FAKE_TOKEN, evidence)

    def test_local_success_runs_agent_and_docker_then_completes_without_delivery(self):
        job_id = self.create_job()
        flow, agent, docker, git_ops, github = self.build_flow()
        repo_path = self.root / "local-fixture"
        (repo_path / ".git").mkdir(parents=True)

        result = flow.run_local(
            job_id=job_id,
            worker_token=self.token,
            repo_path=repo_path,
            task="implement",
            test_command="pytest -q",
            timeout_seconds=60,
        )

        self.assertEqual(result.state, "completed")
        self.assertEqual(agent.calls, 1)
        self.assertEqual(docker.calls, 1)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)
        self.assertEqual(flow.broker.calls, 0)
        job = self.cp.get_job(job_id)
        self.assertEqual(job["state"], "completed")
        self.assertIsNone(job["lease_expires"])

    def test_agent_failure_skips_test_commit_and_push_and_releases_lease(self):
        job_id = self.create_job()
        flow, _, docker, git_ops, github = self.build_flow(agent_exit=9)
        result = flow.run(
            job_id, self.token, REPO, "main", "task", "delivery-1",
            "pytest -q", 60,
        )
        self.assertEqual(result.state, "AGENT_FAILED")
        self.assertEqual(docker.calls, 0)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)
        job = self.cp.get_job(job_id)
        self.assertEqual(job["worker_token_hash"], hash_token(self.token))
        self.assertIsNone(job["lease_expires"])

    def test_docker_failure_blocks_commit_and_push(self):
        job_id = self.create_job()
        flow, _, docker, git_ops, github = self.build_flow(test_exit=1)
        result = flow.run(
            job_id, self.token, REPO, "main", "task", "delivery-1",
            "pytest -q", 60,
        )
        self.assertEqual(result.state, "TEST_FAILED")
        self.assertEqual(docker.calls, 1)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)

    def test_no_changes_does_not_create_empty_commit_or_push(self):
        job_id = self.create_job()
        flow, _, docker, git_ops, github = self.build_flow(changed_files=[])
        result = flow.run(
            job_id, self.token, REPO, "main", "task", "delivery-1",
            "pytest -q", 60,
        )
        self.assertEqual(result.state, "AGENT_NO_CHANGES")
        self.assertEqual(docker.calls, 0)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)
        job = self.cp.get_job(job_id)
        self.assertEqual(job["worker_token_hash"], hash_token(self.token))
        self.assertIsNone(job["lease_expires"])
