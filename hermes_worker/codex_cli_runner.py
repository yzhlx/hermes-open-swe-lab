"""Host-side Codex CLI runner with a credential-free, allowlisted environment."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .redact import redact


CODEX_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "CODEX_HOME",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    # Required by executable lookup and Windows system-process startup.
    "PATHEXT",
    "WINDIR",
)

FORBIDDEN_ENV_MARKERS = (
    "GITHUB_APP",
    "INSTALLATION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "JWT",
    "API_KEY",
    "APIKEY",
    "WEBHOOK_SECRET",
    "PRIVATE_KEY",
    "CLIENT_SECRET",
)

_SENSITIVE_JSON_KEYS = {
    "authorization",
    "token",
    "jwt",
    "api_key",
    "apikey",
    "private_key",
    "secret",
    "encrypted_content",
}


@dataclass(frozen=True)
class CodexRunResult:
    exit_code: int
    timed_out: bool
    duration_seconds: float
    events_jsonl_path: str
    final_message_path: str
    stdout_summary: str
    stderr_summary: str
    changed_files: list[str]
    command_redacted: str
    diagnostic_dir: str = ""
    workspace_evidence_path: str = ""
    workspace_binding_ok: bool = True


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _summary(value: str, limit: int = 2000) -> str:
    clean = redact(_text(value))
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return " | ".join(lines[-12:])[:limit]


def _contains_reasoning_event(value) -> bool:
    if isinstance(value, dict):
        event_type = str(value.get("type", "")).lower()
        if "reasoning" in event_type:
            return True
        item = value.get("item")
        if isinstance(item, dict) and "reasoning" in str(item.get("type", "")).lower():
            return True
    return False


def _sanitize_json(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if key.lower() in _SENSITIVE_JSON_KEYS:
                clean[key] = "***REDACTED***"
            else:
                clean[key] = _sanitize_json(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


class CodexCliRunner:
    """Execute ``codex exec`` on the host inside one isolated Git worktree.

    The prompt is written only to stdin. The child environment is built from an
    explicit allowlist and cannot inherit Worker, GitHub App, provider, or
    webhook credentials.
    """

    def __init__(
        self,
        codex_binary: str = "codex",
        *,
        popen_factory: Callable = subprocess.Popen,
        environ: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.monotonic,
        tree_terminator: Optional[Callable] = None,
        artifacts_root: Optional[Path] = None,
    ):
        self.codex_binary = codex_binary
        self._popen = popen_factory
        self._environ = dict(os.environ if environ is None else environ)
        self._clock = clock
        self._tree_terminator = tree_terminator or self._terminate_process_tree
        self._artifacts_root = Path(
            artifacts_root
            if artifacts_root is not None
            else Path(tempfile.gettempdir()) / "hermes-codex-diagnostics"
        )

    def _child_environment(self) -> dict[str, str]:
        by_upper = {key.upper(): (key, value) for key, value in self._environ.items()}
        child: dict[str, str] = {}
        for allowed in CODEX_ENV_ALLOWLIST:
            found = by_upper.get(allowed)
            if found is not None:
                child[allowed] = found[1]
        # Defense in depth if the allowlist changes later.
        for key in list(child):
            upper = key.upper()
            if any(marker in upper for marker in FORBIDDEN_ENV_MARKERS):
                child.pop(key, None)
        child["NO_COLOR"] = "1"
        return child

    @staticmethod
    def _terminate_process_tree(process) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                process.kill()
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()

    @staticmethod
    def _ensure_artifacts_ignored(repo_path: Path) -> None:
        exclude = repo_path / ".git" / "info" / "exclude"
        if not exclude.parent.is_dir():
            return
        current = exclude.read_text(encoding="utf-8", errors="replace") \
            if exclude.exists() else ""
        if ".hermes/" not in {line.strip() for line in current.splitlines()}:
            with exclude.open("a", encoding="utf-8", newline="\n") as stream:
                if current and not current.endswith(("\n", "\r")):
                    stream.write("\n")
                stream.write(".hermes/\n")

    @staticmethod
    def _capture_events(stdout: str, path: Path) -> tuple[int, int, int, list[str]]:
        accepted = 0
        invalid = 0
        reasoning_omitted = 0
        event_types: list[str] = []
        output: list[str] = []
        for raw in _text(stdout).splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if _contains_reasoning_event(event):
                reasoning_omitted += 1
                continue
            sanitized = _sanitize_json(event)
            output.append(json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")))
            accepted += 1
            if isinstance(event, dict):
                event_types.append(str(event.get("type", "unknown")))
        path.write_text(
            ("\n".join(output) + "\n") if output else "",
            encoding="utf-8",
        )
        return accepted, invalid, reasoning_omitted, event_types

    @staticmethod
    def _changed_files(repo_path: Path) -> list[str]:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        raw = result.stdout.decode("utf-8", errors="replace")
        records = raw.split("\0")
        changed: list[str] = []
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
            changed.append(normalized)
        return sorted(set(changed))

    def run(
        self,
        repo_path: Path,
        prompt: str,
        timeout_seconds: int,
    ) -> CodexRunResult:
        repo_path = Path(repo_path).resolve()
        if not repo_path.is_dir():
            raise ValueError("repo_path must be an existing isolated task directory")
        if any((parent / ".git").exists()
               for parent in (repo_path.parent, *repo_path.parent.parents)):
            raise ValueError(
                "isolated task repository must not be nested in another Git repository"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        artifacts_root = self._artifacts_root.resolve()
        try:
            artifacts_root.relative_to(repo_path)
        except ValueError:
            pass
        else:
            raise ValueError("Codex diagnostic artifacts must be outside repo_path")
        if any((parent / ".git").exists()
               for parent in (artifacts_root, *artifacts_root.parents)):
            raise ValueError("Codex diagnostic artifacts must be outside Git repositories")

        run_dir = artifacts_root / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        events_path = run_dir / "events.jsonl"
        final_message_path = run_dir / "final-message.txt"
        workspace_evidence_path = run_dir / "workspace-evidence.json"
        command = [
            self.codex_binary,
            "--ask-for-approval",
            "never",
            "exec",
            "--cd",
            str(repo_path),
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--json",
            "--output-last-message",
            str(final_message_path),
            "-",
        ]
        command_redacted = redact(subprocess.list2cmdline(command))
        git_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        git_root_text = git_root_result.stdout.strip()
        try:
            git_root = Path(git_root_text).resolve()
        except (OSError, ValueError):
            git_root = Path()
        popen_cwd = repo_path
        binding_ok = (
            git_root_result.returncode == 0
            and os.path.normcase(str(repo_path))
            == os.path.normcase(str(popen_cwd))
            == os.path.normcase(str(git_root))
        )
        workspace_evidence = {
            "repo_path": redact(str(repo_path)),
            "popen_cwd": redact(str(popen_cwd)),
            "git_root": redact(str(git_root_text)),
            "branch": redact(branch_result.stdout.strip()),
            "initial_status": redact(status_result.stdout),
            "command": command_redacted,
            "binding_ok": binding_ok,
        }
        workspace_evidence_path.write_text(
            json.dumps(workspace_evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not binding_ok:
            events_path.write_text("", encoding="utf-8")
            final_message_path.write_text("", encoding="utf-8")
            return CodexRunResult(
                exit_code=125,
                timed_out=False,
                duration_seconds=0.0,
                events_jsonl_path=str(events_path),
                final_message_path=str(final_message_path),
                stdout_summary="events=0; workspace_binding=false",
                stderr_summary="WORKSPACE_BINDING_FAILURE",
                changed_files=[],
                command_redacted=command_redacted,
                diagnostic_dir=str(run_dir),
                workspace_evidence_path=str(workspace_evidence_path),
                workspace_binding_ok=False,
            )
        child_env = self._child_environment()
        popen_kwargs = {
            "cwd": str(repo_path),
            "env": child_env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        started = self._clock()
        timed_out = False
        stdout = ""
        stderr = ""
        exit_code = 127
        try:
            process = self._popen(command, **popen_kwargs)
            try:
                stdout, stderr = process.communicate(
                    input=prompt, timeout=timeout_seconds
                )
                exit_code = int(process.returncode)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                self._tree_terminator(process)
                tail_out, tail_err = process.communicate()
                stdout = _text(exc.output) + _text(tail_out)
                stderr = _text(exc.stderr) + _text(tail_err)
                exit_code = 124
        except FileNotFoundError:
            stderr = "Codex CLI executable not found"
            exit_code = 127

        accepted, invalid, omitted, event_types = self._capture_events(
            stdout, events_path
        )
        if final_message_path.exists():
            final_text = redact(final_message_path.read_text(
                encoding="utf-8", errors="replace"
            ))
            final_message_path.write_text(final_text, encoding="utf-8")
        else:
            final_message_path.write_text("", encoding="utf-8")

        counts: dict[str, int] = {}
        for event_type in event_types:
            counts[event_type] = counts.get(event_type, 0) + 1
        type_summary = ",".join(
            f"{name}:{count}" for name, count in sorted(counts.items())
        )
        stdout_summary = (
            f"events={accepted}; invalid={invalid}; reasoning_omitted={omitted}; "
            f"types={type_summary or 'none'}; "
            f"final_message_present={str(bool(final_message_path.stat().st_size)).lower()}"
        )
        return CodexRunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=max(0.0, self._clock() - started),
            events_jsonl_path=str(events_path),
            final_message_path=str(final_message_path),
            stdout_summary=stdout_summary,
            stderr_summary=_summary(stderr),
            changed_files=self._changed_files(repo_path),
            command_redacted=command_redacted,
            diagnostic_dir=str(run_dir),
            workspace_evidence_path=str(workspace_evidence_path),
            workspace_binding_ok=True,
        )
