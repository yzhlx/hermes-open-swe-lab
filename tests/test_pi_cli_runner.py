from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock

import pytest

from hermes_worker.pi_cli_runner import PiCliRunner


class FakeProcess:
    def __init__(self, owner, returncode=0, stdout="", stderr="", timeout=False):
        self.owner = owner
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.pid = 4242
        self.calls = 0

    def communicate(self, input=None, timeout=None):
        self.owner.stdin = input
        self.owner.timeout = timeout
        self.calls += 1
        if self.timeout and self.calls == 1:
            raise subprocess.TimeoutExpired(self.owner.args, timeout)
        if self.owner.on_communicate:
            self.owner.on_communicate(Path(self.owner.cwd))
        return self.stdout, self.stderr

    def kill(self):
        self.owner.killed = True
        self.returncode = 124


class FakePopen:
    def __init__(self, *, returncode=0, stdout="", stderr="", timeout=False,
                 on_communicate=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout_mode = timeout
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
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def runner_fixture(root: Path, fake: FakePopen, environ=None) -> tuple[PiCliRunner, Path]:
    binary = root / ("pi-test.cmd" if os.name == "nt" else "pi-test")
    binary.write_text("test", encoding="utf-8")
    agent_dir = root / "pi-agent"
    agent_dir.mkdir()
    extension = root / "trusted-extension.ts"
    extension.write_text("export default function() {}\n", encoding="utf-8")
    runner = PiCliRunner(
        str(binary),
        provider="test-provider",
        model="test-model",
        thinking="high",
        agent_dir=agent_dir,
        extension_path=extension,
        popen_factory=fake,
        environ=environ or {"PATH": os.environ.get("PATH", "")},
        tree_terminator=lambda process: process.kill(),
        artifacts_root=root / "diagnostics",
    )
    return runner, binary


def submit_event(status="completed", summary="done") -> str:
    return json.dumps({
        "type": "tool_execution_end",
        "toolCallId": "result-1",
        "toolName": "submit_result",
        "result": {
            "content": [{"type": "text", "text": "submitted"}],
            "details": {"status": status, "summary": summary},
        },
        "isError": False,
    })



def test_windows_resolve_binary_prefers_cmd_sibling_for_posix_shim():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shim = root / "pi"
        cmd = root / "pi.cmd"
        bat = root / "pi.bat"
        exe = root / "pi.exe"
        shim.write_text("#!/bin/sh\n", encoding="utf-8")
        cmd.write_text("@echo off\r\n", encoding="utf-8")
        with (
            mock.patch("hermes_worker.pi_cli_runner.os.name", "nt"),
            mock.patch(
                "hermes_worker.pi_cli_runner.shutil.which",
                return_value=str(shim),
            ),
        ):
            assert PiCliRunner.resolve_binary(str(shim)) == str(cmd.resolve())
            cmd.unlink()
            bat.write_text("@echo off\r\n", encoding="utf-8")
            assert PiCliRunner.resolve_binary(str(shim)) == str(bat.resolve())
            bat.unlink()
            exe.write_text("launcher\n", encoding="utf-8")
            assert PiCliRunner.resolve_binary(str(shim)) == str(exe.resolve())


def test_resolve_binary_keeps_bare_suffixed_and_non_windows_semantics():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shim = root / "pi"
        cmd = root / "pi.cmd"
        exe = root / "custom.exe"
        for path in (shim, cmd, exe):
            path.write_text("launcher\n", encoding="utf-8")

        with (
            mock.patch("hermes_worker.pi_cli_runner.os.name", "nt"),
            mock.patch(
                "hermes_worker.pi_cli_runner.shutil.which",
                return_value=str(cmd),
            ) as which,
        ):
            assert PiCliRunner.resolve_binary("pi") == str(cmd.resolve())
            which.assert_called_once_with("pi")

        with (
            mock.patch("hermes_worker.pi_cli_runner.os.name", "nt"),
            mock.patch(
                "hermes_worker.pi_cli_runner.shutil.which",
                return_value=str(exe),
            ),
        ):
            assert PiCliRunner.resolve_binary(str(exe)) == str(exe.resolve())

        with (
            mock.patch("hermes_worker.pi_cli_runner.os") as runner_os,
            mock.patch(
                "hermes_worker.pi_cli_runner.shutil.which",
                return_value=str(shim),
            ),
        ):
            runner_os.name = "posix"
            assert PiCliRunner.resolve_binary(str(shim)) == str(shim.resolve())


def test_windows_cmd_launch_is_wrapped_by_comspec():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen(stdout=submit_event())
        runner, binary = runner_fixture(root, fake, {
            "PATH": os.environ.get("PATH", ""),
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
        })
        with mock.patch("hermes_worker.pi_cli_runner.os.name", "nt"):
            result = runner.run(repo, "prompt stays on stdin", 10)
        assert result.exit_code == 0
        assert fake.args[:4] == [
            r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c",
        ]
        assert fake.args[4] == str(binary.resolve())
        bat_command = runner._command(str(binary.with_suffix(".bat")))
        assert bat_command[:4] == [
            r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c",
        ]
        assert bat_command[4] == str(binary.with_suffix(".bat"))
        assert "prompt stays on stdin" not in fake.args
        assert fake.stdin == "prompt stays on stdin"


def test_command_stdin_environment_and_sanitized_success_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        events = "\n".join([
            json.dumps({"type": "session", "version": 3, "cwd": str(repo)}),
            json.dumps({
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "reasoning", "text": "hidden"}],
                },
            }),
            json.dumps({
                "type": "tool_execution_end",
                "toolName": "write",
                "result": {"details": {"token": "ghs_FORBIDDEN_VALUE"}},
                "isError": False,
            }),
            submit_event(summary="finished"),
        ])
        fake = FakePopen(stdout=events)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "USERPROFILE": str(root),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", "cmd.exe"),
            "HERMES_WORKER_TOKEN": "forbidden",
            "GITHUB_TOKEN": "ghs_FORBIDDEN_VALUE",
            "OPENAI_API_KEY": "sk-forbidden-value",
            "PI_PROVIDER": "parent-provider",
            "PI_MODEL": "parent-model",
        }
        runner, binary = runner_fixture(root, fake, environment)
        result = runner.run(repo, "implement from stdin", 30)

        assert result.exit_code == 0
        assert result.timed_out is False
        assert fake.stdin == "implement from stdin"
        assert fake.cwd == str(repo.resolve())
        assert "implement from stdin" not in fake.args
        for flag in (
            "--mode", "json", "--no-session", "--no-context-files",
            "--no-skills", "--no-prompt-templates", "--no-extensions",
            "--no-builtin-tools", "--no-approve", "--provider",
            "test-provider", "--model", "test-model", "--thinking", "high",
        ):
            assert flag in fake.args
        assert str(binary.resolve()) in fake.args
        assert fake.env["PI_SKIP_VERSION_CHECK"] == "1"
        assert fake.env["PI_TELEMETRY"] == "0"
        assert fake.env["HERMES_PI_WORKSPACE_ROOT"] == str(repo.resolve())
        assert Path(fake.env["PI_CODING_AGENT_DIR"]).name == "pi-agent"
        for key in (
            "HERMES_WORKER_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY",
            "PI_PROVIDER", "PI_MODEL",
        ):
            assert key not in fake.env
        stored = Path(result.events_jsonl_path).read_text(encoding="utf-8")
        assert "hidden" not in stored
        assert "ghs_FORBIDDEN_VALUE" not in stored
        assert '"content"' not in stored
        assert '"result"' not in stored
        assert '"toolName":"write"' in stored
        assert "reasoning_omitted=1" in result.stdout_summary
        assert Path(result.final_message_path).read_text(encoding="utf-8") == "finished"
        assert "implement from stdin" not in result.command_redacted
        assert result.provider == "test-provider"
        assert result.model == "test-model"


def test_exit_zero_without_submit_result_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen(stdout=json.dumps({"type": "agent_end", "messages": []}))
        runner, _ = runner_fixture(root, fake)
        result = runner.run(repo, "task", 10)
        assert result.exit_code == 126
        assert result.stderr_summary == "PI_RESULT_MISSING"
        assert "result_submitted=false" in result.stdout_summary


def test_blocked_submit_result_is_nonzero():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen(stdout=submit_event(status="blocked", summary="cannot edit"))
        runner, _ = runner_fixture(root, fake)
        result = runner.run(repo, "task", 10)
        assert result.exit_code == 1
        assert result.stderr_summary == "PI_AGENT_BLOCKED"


def test_timeout_terminates_process_tree():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen(timeout=True)
        terminated = []
        runner, _ = runner_fixture(root, fake)
        runner._tree_terminator = lambda process: (terminated.append(process.pid), process.kill())
        result = runner.run(repo, "task", 1)
        assert result.timed_out is True
        assert result.exit_code == 124
        assert terminated == [4242]


def test_workspace_binding_mismatch_fails_before_pi_launch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen()
        runner, _ = runner_fixture(root, fake)
        real_run = subprocess.run

        def mismatched_git_root(args, **kwargs):
            if args == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(
                    args, 0, str(root / "different-repo") + "\n", ""
                )
            return real_run(args, **kwargs)

        with mock.patch(
            "hermes_worker.pi_cli_runner.subprocess.run",
            side_effect=mismatched_git_root,
        ):
            result = runner.run(repo, "task", 10)
        assert result.exit_code == 125
        assert result.workspace_binding_ok is False
        assert fake.args is None


def test_changed_files_are_host_collected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)

        def edit(worktree: Path):
            feature = worktree / "new-directory" / "feature.py"
            feature.parent.mkdir()
            feature.write_text("VALUE = 1\n", encoding="utf-8")

        fake = FakePopen(stdout=submit_event(), on_communicate=edit)
        runner, _ = runner_fixture(root, fake)
        result = runner.run(repo, "task", 10)
        assert result.changed_files == ["new-directory/feature.py"]


def test_rejects_secret_shaped_allowlisted_environment_value():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root)
        fake = FakePopen(stdout=submit_event())
        runner, _ = runner_fixture(root, fake, {
            "PATH": f"C:\\safe\\ghp_{'A' * 40}",
        })
        with pytest.raises(
            ValueError,
            match="credential_like_content_forbidden_in_pi_environment",
        ):
            runner.run(repo, "task", 10)


def test_workspace_guard_node_suite_passes():
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/node/pi_workspace_guard.test.mjs"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_extension_contract_has_no_shell_or_network_tool():
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / "deploy/worker/pi_workspace_extension.ts").read_text(
        encoding="utf-8"
    )
    assert "pi.exec(" not in text
    assert "child_process" not in text
    assert "registerTool({\n    name: \"bash\"" not in text
    assert "fetch(" not in text
    for name in ("read", "write", "edit", "ls", "find", "grep", "submit_result"):
        assert f'name: "{name}"' in text
    assert "terminate: true" in text
    assert 'pi.on("session_start"' in text
    session_start_index = text.index('pi.on("session_start"')
    set_active_index = text.index("pi.setActiveTools(TOOL_NAMES)")
    assert set_active_index > session_start_index


def test_extension_loads_offline_without_provider_call():
    repo_root = Path(__file__).resolve().parents[1]
    pi = shutil.which("pi")
    if not pi:
        pytest.skip("pi unavailable")
    args = [
        str(Path(pi).resolve()),
        "--no-session", "--no-context-files", "--no-skills",
        "--no-prompt-templates", "--no-extensions", "--no-builtin-tools",
        "--no-approve", "-e", "deploy/worker/pi_workspace_extension.ts",
        "--list-models", "no-such-model",
    ]
    if os.name == "nt" and Path(args[0]).suffix.lower() in {".cmd", ".bat"}:
        args = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *args]
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "TEMP", "TMP"}
    }
    env.update({
        "HERMES_PI_WORKSPACE_ROOT": str(repo_root),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "PI_SKIP_VERSION_CHECK": "1",
    })
    result = subprocess.run(
        args,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Failed to load extension" not in result.stderr
