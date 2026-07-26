from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hermes_worker.codex_cli_runner import CodexCliRunner, CodexRunResult
from hermes_worker.codex_job_runner import CodexJobRunner, main as codex_job_main
from hermes_worker.control_plane import ControlPlane
from hermes_worker.db import hash_token
from hermes_worker.docker_sandbox import DockerTestResult, HermesDockerSandboxBackend
from hermes_worker.repository import (
    CommandResult,
    CommitResult,
    RepositoryPrepareResult,
    RepositoryPreparer,
)


REPO = "yzhlx/hermes-open-swe-smoke-test"
FAKE_TOKEN = "ghs_FAKE_INSTALLATION_TOKEN_123456"


class FakeProcess:
    def __init__(self, owner, returncode=0, stdout="", stderr="", timeout=False):
        self.owner = owner
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.pid = 4242
        self._communicates = 0

    def communicate(self, input=None, timeout=None):
        self.owner.stdin = input
        self.owner.timeout = timeout
        self._communicates += 1
        if self.timeout and self._communicates == 1:
            raise subprocess.TimeoutExpired(self.owner.args, timeout)
        if self.owner.on_communicate:
            self.owner.on_communicate(Path(self.owner.cwd))
        return self.stdout, self.stderr

    def kill(self):
        self.owner.killed = True
        self.returncode = 124


class FakePopen:
    def __init__(self, *, returncode=0, stdout="", stderr="", timeout=False,
                 final_message="finished", on_communicate=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout_mode = timeout
        self.final_message = final_message
        self.on_communicate = on_communicate
        self.args = None
        self.kwargs = None
        self.cwd = None
        self.env = None
        self.stdin = None
        self.timeout = None
        self.killed = False

    def __call__(self, args, **kwargs):
        self.args = list(args)
        self.kwargs = dict(kwargs)
        self.cwd = kwargs["cwd"]
        self.env = dict(kwargs["env"])
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.final_message is not None:
            output_path.write_text(self.final_message, encoding="utf-8")
        return FakeProcess(
            self,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            timeout=self.timeout_mode,
        )


def init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Hermes Tests"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True,
                   capture_output=True)
    return repo


class CodexCliRunnerTests(unittest.TestCase):
    def test_command_stdin_allowlist_and_success_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            events = "\n".join([
                json.dumps({"type": "thread.started"}),
                json.dumps({"type": "item.completed",
                            "item": {"type": "reasoning", "text": "hidden"}}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ])
            fake = FakePopen(stdout=events, final_message="done")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "USERPROFILE": str(Path(tmp)),
                "CODEX_HOME": str(Path(tmp) / "codex-home"),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
                "COMSPEC": os.environ.get("COMSPEC", "cmd.exe"),
                "HERMES_GITHUB_APP_ID": "forbidden",
                "HERMES_GITHUB_INSTALLATION_ID": "forbidden",
                "HERMES_GITHUB_APP_PRIVATE_KEY_PATH": "forbidden",
                "GITHUB_TOKEN": FAKE_TOKEN,
                "OPENAI_API_KEY": "sk-forbidden-secret",
                "WEBHOOK_SECRET": "forbidden",
            }
            runner = CodexCliRunner(
                codex_binary="codex-test",
                popen_factory=fake,
                environ=env,
                tree_terminator=lambda process: process.kill(),
            )
            result = runner.run(repo, "implement from stdin", 30)

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(fake.stdin, "implement from stdin")
            self.assertEqual(fake.cwd, str(repo.resolve()))
            self.assertEqual(fake.kwargs["encoding"], "utf-8")
            self.assertEqual(fake.kwargs["errors"], "replace")
            self.assertEqual(fake.args[0:2], ["codex-test", "exec"])
            for arg in ("--sandbox", "workspace-write", "--ephemeral", "--json",
                        "--output-last-message", "-"):
                self.assertIn(arg, fake.args)
            self.assertNotIn("implement from stdin", fake.args)
            for key in (
                "HERMES_GITHUB_APP_ID",
                "HERMES_GITHUB_INSTALLATION_ID",
                "HERMES_GITHUB_APP_PRIVATE_KEY_PATH",
                "GITHUB_TOKEN",
                "OPENAI_API_KEY",
                "WEBHOOK_SECRET",
            ):
                self.assertNotIn(key, fake.env)
            events_text = Path(result.events_jsonl_path).read_text(encoding="utf-8")
            self.assertNotIn("hidden", events_text)
            self.assertIn("reasoning_omitted=1", result.stdout_summary)
            self.assertEqual(
                Path(result.final_message_path).read_text(encoding="utf-8"), "done"
            )
            self.assertNotIn("implement from stdin", result.command_redacted)

    def test_nonzero_exit_is_captured_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            fake = FakePopen(
                returncode=7,
                stderr=f"token={FAKE_TOKEN}",
                final_message="failed",
            )
            result = CodexCliRunner(
                popen_factory=fake,
                environ={"PATH": os.environ.get("PATH", "")},
                tree_terminator=lambda process: process.kill(),
            ).run(repo, "task", 10)
            self.assertEqual(result.exit_code, 7)
            self.assertNotIn(FAKE_TOKEN, result.stderr_summary)

    def test_timeout_terminates_process_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            fake = FakePopen(timeout=True, final_message=None)
            terminated = []

            def terminate(process):
                terminated.append(process.pid)
                process.kill()

            result = CodexCliRunner(
                popen_factory=fake,
                environ={"PATH": os.environ.get("PATH", "")},
                tree_terminator=terminate,
            ).run(repo, "task", 1)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.exit_code, 124)
            self.assertEqual(terminated, [4242])

    def test_changed_files_are_collected_but_runner_artifacts_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))

            def edit(worktree):
                (worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")

            fake = FakePopen(
                stdout=json.dumps({"type": "turn.completed"}),
                on_communicate=edit,
            )
            result = CodexCliRunner(
                popen_factory=fake,
                environ={"PATH": os.environ.get("PATH", "")},
                tree_terminator=lambda process: process.kill(),
            ).run(repo, "task", 10)
            self.assertEqual(result.changed_files, ["feature.py"])
            self.assertFalse(any(path.startswith(".hermes") for path in result.changed_files))


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
                destination, REPO, "main", "codex/job-9", FAKE_TOKEN
            )
            self.assertTrue(result.ok)
            flat_args = [" ".join(call[0]) for call in fake.calls]
            self.assertTrue(any(command.endswith(" init") for command in flat_args))
            self.assertTrue(any(" fetch --depth 1 origin main" in command
                                for command in flat_args))
            self.assertTrue(any(" checkout -b codex/job-9 FETCH_HEAD" in command
                                for command in flat_args))
            self.assertFalse(any(" clone " in f" {command} " for command in flat_args))
            fetch = next(call for call in fake.calls if "fetch" in call[0])
            self.assertEqual(fetch[2]["HERMES_GIT_INSTALLATION_TOKEN"], FAKE_TOKEN)
            self.assertEqual(fetch[2]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(fetch[2]["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertFalse(Path(fetch[2]["GIT_ASKPASS"]).exists())
            serialized = json.dumps(result.commands) + result.stderr_summary
            self.assertNotIn(FAKE_TOKEN, serialized)

    def test_fetch_failure_is_redacted_and_directory_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "task-repo"
            fake = FakeGitCommandRunner(
                fetch_exit=128, fetch_stderr=f"token={FAKE_TOKEN}"
            )
            result = RepositoryPreparer(
                command_runner=fake,
                environ={"PATH": os.environ.get("PATH", "")},
            ).prepare(destination, REPO, "main", "codex/job-9", FAKE_TOKEN)
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


class FakeCodex:
    def __init__(self, exit_code=0, changed_files=None):
        self.exit_code = exit_code
        self.changed_files = ["feature.py"] if changed_files is None else changed_files
        self.calls = 0

    def run(self, repo_path, prompt, timeout_seconds):
        self.calls += 1
        return CodexRunResult(
            exit_code=self.exit_code,
            timed_out=False,
            duration_seconds=0.1,
            events_jsonl_path=str(Path(repo_path) / ".hermes" / "events.jsonl"),
            final_message_path=str(Path(repo_path) / ".hermes" / "final.txt"),
            stdout_summary="events=1",
            stderr_summary="" if self.exit_code == 0 else "codex failed",
            changed_files=list(self.changed_files),
            command_redacted="codex exec --sandbox workspace-write ...",
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


class CodexJobRunnerStateTests(unittest.TestCase):
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

    def build_flow(self, *, codex_exit=0, changed_files=None, test_exit=0):
        codex = FakeCodex(codex_exit, changed_files)
        docker = FakeDockerBackend(test_exit)
        git_ops = FakeGitOperations(changed_files)
        github = FakeGitHub()
        flow = CodexJobRunner(
            control_plane=self.cp,
            token_broker=FakeBroker(),
            codex_runner=codex,
            repository_preparer=FakePreparer(self.root),
            git_operations=git_ops,
            docker_backend_factory=lambda repo_path: docker,
            github_client=github,
            work_root=self.root / "work",
        )
        return flow, codex, docker, git_ops, github

    def test_success_runs_codex_then_docker_commit_push_and_draft_pr(self):
        job_id = self.create_job()
        flow, codex, docker, git_ops, github = self.build_flow()
        result = flow.run(
            job_id=job_id, worker_token=self.token, repo=REPO, base="main",
            task="implement", delivery_id="delivery-1",
            test_command="pytest -q", timeout_seconds=60,
        )
        self.assertEqual(result.state, "PR_CREATED")
        self.assertEqual(codex.calls, 1)
        self.assertEqual(docker.calls, 1)
        self.assertEqual(git_ops.commit_calls, 1)
        self.assertEqual(git_ops.push_calls, 1)
        self.assertEqual(github.pr_calls, 1)
        self.assertEqual(flow.broker.calls, 3)
        event_types = [event["event_type"] for event in self.cp.get_events(job_id)]
        self.assertLess(event_types.index("CODEX_RUNNING"),
                        event_types.index("TESTING"))
        job = self.cp.get_job(job_id)
        self.assertEqual(job["state"], "PR_CREATED")
        self.assertEqual(job["worker_token_hash"], hash_token(self.token))
        self.assertIsNone(job["lease_expires"])
        evidence = json.dumps({
            "job": job,
            "events": self.cp.get_events(job_id),
        })
        self.assertNotIn(FAKE_TOKEN, evidence)

    def test_local_success_runs_codex_and_docker_then_completes_without_delivery(self):
        job_id = self.create_job()
        flow, codex, docker, git_ops, github = self.build_flow()
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
        self.assertEqual(codex.calls, 1)
        self.assertEqual(docker.calls, 1)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)
        self.assertEqual(flow.broker.calls, 0)
        job = self.cp.get_job(job_id)
        self.assertEqual(job["state"], "completed")
        self.assertIsNone(job["lease_expires"])

    def test_codex_failure_skips_test_commit_and_push_and_releases_lease(self):
        job_id = self.create_job()
        flow, _, docker, git_ops, github = self.build_flow(codex_exit=9)
        result = flow.run(
            job_id, self.token, REPO, "main", "task", "delivery-1",
            "pytest -q", 60,
        )
        self.assertEqual(result.state, "CODEX_FAILED")
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
        self.assertEqual(result.state, "CODEX_NO_CHANGES")
        self.assertEqual(docker.calls, 0)
        self.assertEqual(git_ops.commit_calls, 0)
        self.assertEqual(git_ops.push_calls, 0)
        self.assertEqual(github.pr_calls, 0)
        job = self.cp.get_job(job_id)
        self.assertEqual(job["worker_token_hash"], hash_token(self.token))
        self.assertIsNone(job["lease_expires"])


class CodexJobCommandTests(unittest.TestCase):
    def test_dry_run_generates_plan_without_creating_database_or_github_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "must-not-exist.sqlite"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = codex_job_main([
                    "--repo", REPO,
                    "--base", "main",
                    "--task", "implement",
                    "--job-id", "42",
                    "--delivery-id", "delivery-42",
                    "--test-command", "pytest -q",
                    "--codex-binary", os.environ.get("PYTHON", "python"),
                    "--db", str(db),
                    "--dry-run",
                ])
            plan = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(plan["dry_run"])
            self.assertFalse(plan["github_writes"])
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
