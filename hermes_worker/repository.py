"""Host Worker Git operations using ephemeral AskPass credentials.

Repository preparation intentionally uses ``git init`` + authenticated shallow
``fetch``. Installation Tokens never appear in URLs, arguments, Git config, or
returned evidence.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from .constants import ALLOWED_GITHUB_REPOS
from .redact import redact


TOKEN_ENV = "HERMES_GIT_INSTALLATION_TOKEN"
_GIT_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RepositoryPrepareResult:
    ok: bool
    repo_path: Path
    exit_code: int
    stderr_summary: str
    commands: list[str]
    askpass_cleaned: bool
    token_cleared: bool
    cleaned_on_failure: bool


@dataclass(frozen=True)
class CommitResult:
    created: bool
    commit_sha: Optional[str]
    exit_code: int
    stderr_summary: str


def _summary(value: str, limit: int = 2000) -> str:
    clean = redact(value or "")
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return " | ".join(lines[-12:])[:limit]


def _remove_tree(path: Path) -> None:
    def make_writable_and_retry(function, item, _error):
        os.chmod(item, stat.S_IWRITE)
        function(item)

    if path.exists():
        shutil.rmtree(path, onerror=make_writable_and_retry)


def _default_command_runner(args, *, cwd, env, timeout) -> CommandResult:
    try:
        result = subprocess.run(
            list(args),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") \
            if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") \
            if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(
            124,
            stdout,
            stderr + "\n[hermes] git command timed out",
        )
    except FileNotFoundError:
        return CommandResult(127, "", "git executable not found")


class _GitHost:
    def __init__(
        self,
        git_binary: str = "git",
        *,
        command_runner: Callable = _default_command_runner,
        environ: Optional[Mapping[str, str]] = None,
    ):
        self.git_binary = git_binary
        self._command_runner = command_runner
        self._environ = dict(os.environ if environ is None else environ)

    def _base_env(self) -> dict[str, str]:
        by_upper = {key.upper(): value for key, value in self._environ.items()}
        env = {
            key: by_upper[key]
            for key in _GIT_ENV_ALLOWLIST
            if key in by_upper
        }
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Ignore global/system helpers, URL rewrites, and extra headers. Every
        # credential for this operation comes from the one-shot AskPass helper.
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        return env

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path,
        env: Optional[dict[str, str]] = None,
        timeout: int = 180,
    ) -> CommandResult:
        return self._command_runner(
            [self.git_binary, *args],
            cwd=cwd,
            env=env or self._base_env(),
            timeout=timeout,
        )

    @contextmanager
    def _askpass(
        self, parent: Path, token: str
    ) -> Iterator[tuple[dict[str, str], Path]]:
        helper_dir = Path(tempfile.mkdtemp(prefix=".hermes-askpass-", dir=parent))
        if os.name == "nt":
            helper = helper_dir / "git-askpass.cmd"
            helper.write_text(
                "@echo off\r\n"
                "setlocal DisableDelayedExpansion\r\n"
                "echo(%~1|%SystemRoot%\\System32\\findstr.exe /I /C:\"Username\" >nul\r\n"
                "if not errorlevel 1 (\r\n"
                "  echo x-access-token\r\n"
                "  exit /b 0\r\n"
                ")\r\n"
                f"if not defined {TOKEN_ENV} exit /b 1\r\n"
                f"echo(%{TOKEN_ENV}%\r\n",
                encoding="ascii",
            )
        else:
            helper = helper_dir / "git-askpass.sh"
            helper.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
                f"  *) printenv {TOKEN_ENV} ;;\n"
                "esac\n",
                encoding="ascii",
            )
            helper.chmod(0o700)
        env = self._base_env()
        env["GIT_ASKPASS"] = str(helper)
        env[TOKEN_ENV] = token
        try:
            yield env, helper
        finally:
            env[TOKEN_ENV] = ""
            env.pop(TOKEN_ENV, None)
            env.pop("GIT_ASKPASS", None)
            _remove_tree(helper_dir)


class RepositoryPreparer(_GitHost):
    def prepare(
        self,
        destination: Path,
        repo: str,
        base_branch: str,
        task_branch: str,
        token: str,
        *,
        resume_existing_branch: bool = False,
    ) -> RepositoryPrepareResult:
        destination = Path(destination).resolve()
        commands: list[str] = []
        last = CommandResult(0, "", "")
        askpass_cleaned = True
        token_cleared = False
        cleaned_on_failure = False

        if repo not in ALLOWED_GITHUB_REPOS:
            return RepositoryPrepareResult(
                False, destination, 2, "repo_not_allowed", commands,
                True, True, False,
            )
        if (
            not _SAFE_REF_RE.fullmatch(base_branch)
            or not _SAFE_REF_RE.fullmatch(task_branch)
            or ".." in base_branch
            or ".." in task_branch
        ):
            return RepositoryPrepareResult(
                False, destination, 2, "invalid_git_ref", commands,
                True, True, False,
            )
        if destination.exists():
            return RepositoryPrepareResult(
                False, destination, 2, "target_directory_exists", commands,
                True, True, False,
            )
        if any((parent / ".git").exists()
               for parent in (destination.parent, *destination.parent.parents)):
            return RepositoryPrepareResult(
                False, destination, 2,
                "task_directory_must_be_outside_other_git_repositories",
                commands, True, True, False,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir()
        url = f"https://github.com/{repo}.git"

        def execute(args: list[str], env: Optional[dict[str, str]] = None) -> bool:
            nonlocal last
            commands.append(redact(subprocess.list2cmdline([self.git_binary, *args])))
            last = self._run(args, cwd=destination, env=env)
            return last.exit_code == 0

        try:
            if not execute(["init"]):
                raise RuntimeError("git_init_failed")
            if not execute(["remote", "add", "origin", url]):
                raise RuntimeError("git_remote_add_failed")

            exclude = destination / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(".hermes/\n")

            helper_path: Optional[Path] = None
            with self._askpass(destination.parent, token) as (git_env, helper):
                helper_path = helper
                fetch_ref = (
                    task_branch if resume_existing_branch else base_branch
                )
                if not execute(
                    [
                        "-c",
                        "credential.helper=",
                        "-c",
                        "credential.useHttpPath=true",
                        "fetch",
                        "--depth",
                        "1",
                        "origin",
                        fetch_ref,
                    ],
                    git_env,
                ):
                    raise RuntimeError("git_fetch_failed")
            askpass_cleaned = helper_path is None or not helper_path.exists()
            token_cleared = TOKEN_ENV not in git_env

            checkout_mode = "-B" if resume_existing_branch else "-b"
            if not execute(
                ["checkout", checkout_mode, task_branch, "FETCH_HEAD"]
            ):
                raise RuntimeError("git_checkout_failed")
            return RepositoryPrepareResult(
                True,
                destination,
                0,
                "",
                commands,
                askpass_cleaned,
                token_cleared,
                False,
            )
        except (RuntimeError, OSError) as exc:
            _remove_tree(destination)
            cleaned_on_failure = not destination.exists()
            exit_code = last.exit_code if last.exit_code != 0 else 1
            stderr = last.stderr or type(exc).__name__
            return RepositoryPrepareResult(
                False,
                destination,
                exit_code,
                _summary(stderr),
                commands,
                askpass_cleaned,
                True,
                cleaned_on_failure,
            )


class HostGitOperations(_GitHost):
    def changed_files(self, repo_path: Path) -> list[str]:
        result = self._run(
            ["status", "--porcelain=v1", "-z"],
            cwd=Path(repo_path),
        )
        if result.exit_code != 0:
            return []
        records = result.stdout.split("\0")
        files: list[str] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            path = record[3:] if len(record) >= 4 else record
            if record[:2] in {"R ", "C ", "RM", "CM"} and index < len(records):
                path = records[index]
                index += 1
            normalized = path.replace("\\", "/")
            if normalized == ".hermes" or normalized.startswith(".hermes/"):
                continue
            files.append(normalized)
        return sorted(set(files))

    def commit(self, repo_path: Path, message: str) -> CommitResult:
        repo_path = Path(repo_path)
        add = self._run(["add", "--all"], cwd=repo_path)
        if add.exit_code != 0:
            return CommitResult(False, None, add.exit_code, _summary(add.stderr))
        staged = self._run(["diff", "--cached", "--quiet"], cwd=repo_path)
        if staged.exit_code == 0:
            return CommitResult(False, None, 0, "")
        if staged.exit_code != 1:
            return CommitResult(False, None, staged.exit_code, _summary(staged.stderr))
        commit = self._run(
            [
                "-c",
                "user.name=hermes-open-swe-lab-yzhlx[bot]",
                "-c",
                "user.email=hermes-open-swe-lab-yzhlx[bot]@users.noreply.github.com",
                "commit",
                "-m",
                message,
            ],
            cwd=repo_path,
        )
        if commit.exit_code != 0:
            return CommitResult(False, None, commit.exit_code, _summary(commit.stderr))
        revision = self._run(["rev-parse", "HEAD"], cwd=repo_path)
        sha = revision.stdout.strip()
        if revision.exit_code != 0 or len(sha) != 40:
            return CommitResult(False, None, revision.exit_code, _summary(revision.stderr))
        return CommitResult(True, sha, 0, "")

    def push(self, repo_path: Path, branch: str, token: str) -> CommandResult:
        repo_path = Path(repo_path).resolve()
        if not _SAFE_REF_RE.fullmatch(branch) or ".." in branch:
            return CommandResult(2, "", "invalid_git_ref")
        with self._askpass(repo_path.parent, token) as (git_env, _helper):
            result = self._run(
                [
                    "-c",
                    "credential.helper=",
                    "-c",
                    "credential.useHttpPath=true",
                    "push",
                    "origin",
                    f"HEAD:refs/heads/{branch}",
                ],
                cwd=repo_path,
                env=git_env,
            )
        return CommandResult(
            result.exit_code,
            _summary(result.stdout),
            _summary(result.stderr),
        )
