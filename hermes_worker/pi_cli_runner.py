"""Host-side Pi CLI runner with contained tools and value-safe artifacts."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .redact import redact


PI_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "PI_PACKAGE_DIR",
)

FORBIDDEN_ENV_MARKERS = (
    "HERMES_",
    "GITHUB",
    "GH_TOKEN",
    "INSTALLATION_TOKEN",
    "JWT",
    "API_KEY",
    "APIKEY",
    "WEBHOOK_SECRET",
    "PRIVATE_KEY",
    "CLIENT_SECRET",
    "PI_SESSION",
    "PI_PROVIDER",
    "PI_MODEL",
    "PI_REASONING",
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

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)


@dataclass(frozen=True)
class AgentRunResult:
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
    provider: str = ""
    model: str = ""


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
        if "reasoning" in event_type or "thinking" in event_type:
            return True
        if any(_contains_reasoning_event(item) for item in value.values()):
            return True
    if isinstance(value, list):
        return any(_contains_reasoning_event(item) for item in value)
    return False


def _sanitize_json(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_JSON_KEYS:
                clean[key] = "***REDACTED***"
            else:
                clean[key] = _sanitize_json(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return redact(_PRIVATE_KEY_BLOCK_RE.sub(
            "[REDACTED PRIVATE KEY BLOCK]",
            value,
        ))
    return value


def _safe_prompt(value: str, limit: int = 32000) -> str:
    return redact(_PRIVATE_KEY_BLOCK_RE.sub(
        "[REDACTED PRIVATE KEY BLOCK]",
        _text(value),
    ))[:limit]


def _artifact_event(event: dict) -> dict:
    """Persist event metadata only, never prompts, messages, args, or results."""
    clean = {"type": str(event.get("type", "unknown"))}
    for key in (
        "toolCallId",
        "toolName",
        "isError",
        "attempt",
        "maxAttempts",
        "reason",
        "aborted",
        "willRetry",
        "success",
    ):
        value = event.get(key)
        if isinstance(value, (str, int, bool)):
            clean[key] = _sanitize_json(value)
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("role"), str):
        clean["messageRole"] = message["role"]
    messages = event.get("messages")
    if isinstance(messages, list):
        clean["messageCount"] = len(messages)
    tool_results = event.get("toolResults")
    if isinstance(tool_results, list):
        clean["toolResultCount"] = len(tool_results)
    return clean


class PiCliRunner:
    """Execute Pi against one isolated Git worktree with contained tools only."""

    def __init__(
        self,
        pi_binary: str,
        *,
        provider: str,
        model: str,
        thinking: str,
        agent_dir: Path,
        extension_path: Path,
        popen_factory: Callable = subprocess.Popen,
        environ: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.monotonic,
        tree_terminator: Optional[Callable] = None,
        artifacts_root: Optional[Path] = None,
    ):
        if not provider.strip() or not model.strip():
            raise ValueError("pi_provider_model_required")
        if thinking not in {
            "off", "minimal", "low", "medium", "high", "xhigh", "max"
        }:
            raise ValueError("invalid_pi_thinking")
        self.pi_binary = str(pi_binary)
        self.provider = provider.strip()
        self.model = model.strip()
        self.thinking = thinking
        self.agent_dir = Path(agent_dir).resolve()
        self.extension_path = Path(extension_path).resolve()
        self._popen = popen_factory
        self._environ = dict(os.environ if environ is None else environ)
        self._clock = clock
        self._tree_terminator = tree_terminator or self._terminate_process_tree
        self._artifacts_root = Path(
            artifacts_root
            if artifacts_root is not None
            else Path(tempfile.gettempdir()) / "hermes-pi-diagnostics"
        )

    @staticmethod
    def resolve_binary(binary: str) -> str:
        path = Path(binary)
        if os.name == "nt" and path.suffix == "" and path.parent != Path("."):
            for suffix in (".cmd", ".bat", ".exe"):
                windows_launcher = path.with_suffix(suffix)
                if windows_launcher.is_file():
                    return str(windows_launcher.resolve())
        candidate = shutil.which(binary)
        if candidate:
            return str(Path(candidate).resolve())
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError("pi_binary_unavailable")

    def _child_environment(self, workspace_root: Path) -> dict[str, str]:
        by_upper = {key.upper(): (key, value) for key, value in self._environ.items()}
        child: dict[str, str] = {}
        for allowed in PI_ENV_ALLOWLIST:
            found = by_upper.get(allowed)
            if found is None:
                continue
            value = str(found[1])
            upper_value = value.upper()
            if (
                redact(value) != value
                or "-----BEGIN " in upper_value
                and "PRIVATE KEY-----" in upper_value
            ):
                raise ValueError("credential_like_content_forbidden_in_pi_environment")
            child[allowed] = value
        for key in list(child):
            upper = key.upper()
            if any(marker in upper for marker in FORBIDDEN_ENV_MARKERS):
                child.pop(key, None)
        child.update({
            "PI_CODING_AGENT_DIR": str(self.agent_dir),
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
            "HERMES_PI_WORKSPACE_ROOT": str(workspace_root),
            "NO_COLOR": "1",
        })
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
    def _changed_files(repo_path: Path) -> list[str]:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo_path,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        records = result.stdout.decode("utf-8", errors="replace").split("\0")
        changed: list[str] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            path_value = record[3:] if len(record) >= 4 else record
            if record[:2] in {"R ", "C ", "RM", "CM"} and index < len(records):
                path_value = records[index]
                index += 1
            normalized = path_value.replace("\\", "/")
            if normalized == ".hermes" or normalized.startswith(".hermes/"):
                continue
            changed.append(normalized)
        return sorted(set(changed))

    @staticmethod
    def _capture_events(stdout: str, path: Path) -> tuple[int, int, int, dict]:
        accepted = 0
        invalid = 0
        reasoning_omitted = 0
        output: list[str] = []
        submitted: dict = {}
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
            if (
                isinstance(event, dict)
                and event.get("type") == "tool_execution_end"
                and event.get("toolName") == "submit_result"
                and not event.get("isError", False)
            ):
                result = event.get("result")
                details = result.get("details") if isinstance(result, dict) else None
                if (
                    isinstance(details, dict)
                    and details.get("status") in {"completed", "blocked"}
                    and isinstance(details.get("summary"), str)
                ):
                    submitted = {
                        "status": details["status"],
                        "summary": _safe_prompt(details["summary"], 4000),
                    }
            output.append(json.dumps(
                _artifact_event(event),
                ensure_ascii=False,
                separators=(",", ":"),
            ))
            accepted += 1
        path.write_text(
            ("\n".join(output) + "\n") if output else "",
            encoding="utf-8",
        )
        return accepted, invalid, reasoning_omitted, submitted

    def _command(self, resolved_binary: str) -> list[str]:
        args = [
            resolved_binary,
            "--mode", "json",
            "--no-session",
            "--no-context-files",
            "--no-skills",
            "--no-prompt-templates",
            "--no-extensions",
            "--no-builtin-tools",
            "--no-approve",
            "-e", str(self.extension_path),
            "--provider", self.provider,
            "--model", self.model,
            "--thinking", self.thinking,
        ]
        if os.name == "nt" and Path(resolved_binary).suffix.lower() in {".cmd", ".bat"}:
            comspec = self._environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/d", "/s", "/c", *args]
        return args

    def run(
        self,
        repo_path: Path,
        prompt: str,
        timeout_seconds: int,
    ) -> AgentRunResult:
        repo_path = Path(repo_path).resolve()
        if not repo_path.is_dir():
            raise ValueError("repo_path_must_exist")
        if not (repo_path / ".git").is_dir():
            raise ValueError("repo_path_must_be_git_worktree")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds_must_be_positive")
        if not self.agent_dir.is_dir():
            raise RuntimeError("pi_agent_dir_unavailable")
        if not self.extension_path.is_file():
            raise RuntimeError("pi_extension_unavailable")
        try:
            self.extension_path.relative_to(repo_path)
        except ValueError:
            pass
        else:
            raise ValueError("pi_extension_must_be_outside_task_repo")

        artifacts_root = self._artifacts_root.resolve()
        try:
            artifacts_root.relative_to(repo_path)
        except ValueError:
            pass
        else:
            raise ValueError("pi_diagnostics_must_be_outside_task_repo")
        if any(
            (parent / ".git").exists()
            for parent in (artifacts_root, *artifacts_root.parents)
        ):
            raise ValueError("pi_diagnostics_must_be_outside_git_repositories")

        run_dir = artifacts_root / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        events_path = run_dir / "events.jsonl"
        final_message_path = run_dir / "final-message.txt"
        workspace_evidence_path = run_dir / "workspace-evidence.json"

        git_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
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
        binding_ok = (
            git_root_result.returncode == 0
            and os.path.normcase(str(repo_path)) == os.path.normcase(str(git_root))
        )
        if not binding_ok:
            events_path.write_text("", encoding="utf-8")
            final_message_path.write_text("", encoding="utf-8")
            workspace_evidence_path.write_text(json.dumps({
                "repo_path": redact(str(repo_path)),
                "git_root": redact(git_root_text),
                "binding_ok": False,
                "execution_backend": "pi-json-contained-tools",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return AgentRunResult(
                exit_code=125,
                timed_out=False,
                duration_seconds=0.0,
                events_jsonl_path=str(events_path),
                final_message_path=str(final_message_path),
                stdout_summary="events=0; workspace_binding=false",
                stderr_summary="WORKSPACE_BINDING_FAILURE",
                changed_files=[],
                command_redacted="",
                diagnostic_dir=str(run_dir),
                workspace_evidence_path=str(workspace_evidence_path),
                workspace_binding_ok=False,
                provider=self.provider,
                model=self.model,
            )

        resolved_binary = self.resolve_binary(self.pi_binary)
        command = self._command(resolved_binary)
        command_redacted = redact(subprocess.list2cmdline(command))
        workspace_evidence_path.write_text(json.dumps({
            "repo_path": redact(str(repo_path)),
            "popen_cwd": redact(str(repo_path)),
            "git_root": redact(git_root_text),
            "binding_ok": True,
            "execution_backend": "pi-json-contained-tools",
            "provider": self.provider,
            "model": self.model,
            "thinking": self.thinking,
            "extension": redact(str(self.extension_path)),
            "command": command_redacted,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        popen_kwargs = {
            "cwd": str(repo_path),
            "env": self._child_environment(repo_path),
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
                    input=_safe_prompt(prompt),
                    timeout=timeout_seconds,
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
            stderr = "Pi CLI executable not found"
            exit_code = 127

        accepted, invalid, omitted, submitted = self._capture_events(
            stdout, events_path
        )
        if submitted:
            final_message_path.write_text(submitted["summary"], encoding="utf-8")
        else:
            final_message_path.write_text("", encoding="utf-8")

        if exit_code == 0 and not submitted:
            exit_code = 126
            stderr = "PI_RESULT_MISSING"
        elif exit_code == 0 and submitted.get("status") == "blocked":
            exit_code = 1
            stderr = "PI_AGENT_BLOCKED"

        stdout_summary = (
            f"events={accepted}; invalid={invalid}; reasoning_omitted={omitted}; "
            f"result_submitted={str(bool(submitted)).lower()}; "
            f"status={submitted.get('status', 'missing')}"
        )
        return AgentRunResult(
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
            provider=self.provider,
            model=self.model,
        )
