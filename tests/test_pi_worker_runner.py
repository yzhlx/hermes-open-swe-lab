from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.worker import pi_worker_runner as runner


PINNED = (
    "python:3.11-slim@sha256:"
    "db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93"
)


def base_env(tmp_path: Path) -> dict[str, str]:
    agent_dir = tmp_path / "pi-agent"
    agent_dir.mkdir()
    extension = tmp_path / "pi_workspace_extension.ts"
    extension.write_text("export default function() {}\n", encoding="utf-8")
    extension.with_name("pi_workspace_guard.mjs").write_text(
        "export const ok = true;\n", encoding="utf-8"
    )
    return {
        "HERMES_CLOUD_URL": "http://127.0.0.1:28080",
        "HERMES_LOCALHOST_TEST": "1",
        "HERMES_WORKER_TOKEN_FILE": str(tmp_path / "worker-token"),
        "HERMES_PI_BINARY": "pi",
        "HERMES_PI_PROVIDER": "test-provider",
        "HERMES_PI_MODEL": "test-model",
        "HERMES_PI_THINKING": "high",
        "HERMES_PI_AGENT_DIR": str(agent_dir.resolve()),
        "HERMES_PI_EXTENSION_PATH": str(extension.resolve()),
        "HERMES_DOCKER_IMAGE": PINNED,
        "HERMES_TASK_WORK_ROOT": str(tmp_path / "worktrees"),
    }


def test_parser_has_no_backend_or_local_execution_fallback():
    parser = runner.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--backend" not in option_strings
    assert "--sandbox-type" not in option_strings
    assert "--job-id" in option_strings
    assert "--repo" in option_strings
    assert "--dry-run" in option_strings


def test_load_config_requires_https_except_explicit_loopback(tmp_path):
    env = base_env(tmp_path)
    env["HERMES_CLOUD_URL"] = "http://cloud.example.invalid"
    with pytest.raises(ValueError, match="https_required"):
        runner.load_config(env)

    env["HERMES_CLOUD_URL"] = "http://127.0.0.1:28080"
    config = runner.load_config(env)
    assert config["cloud_url"] == "http://127.0.0.1:28080"
    assert config["pi_provider"] == "test-provider"
    assert config["pi_model"] == "test-model"
    assert config["pi_thinking"] == "high"
    assert config["docker_image"] == PINNED
    assert "backend" not in config
    assert "sandbox_type" not in config


@pytest.mark.parametrize(
    "missing",
    ("HERMES_PI_PROVIDER", "HERMES_PI_MODEL", "HERMES_PI_THINKING", "HERMES_PI_AGENT_DIR"),
)
def test_load_config_requires_explicit_pi_identity_and_agent_dir(tmp_path, missing):
    env = base_env(tmp_path)
    env.pop(missing)
    with pytest.raises(ValueError, match="required"):
        runner.load_config(env)


def test_load_config_rejects_mutable_docker_image(tmp_path):
    env = base_env(tmp_path)
    env["HERMES_DOCKER_IMAGE"] = "python:3.11-slim"
    with pytest.raises(ValueError, match="docker_image_digest_required"):
        runner.load_config(env)


def test_dry_run_plan_is_value_free_and_performs_no_writes(tmp_path):
    env = base_env(tmp_path)
    token_file = Path(env["HERMES_WORKER_TOKEN_FILE"])
    token_file.write_text("synthetic-worker-token", encoding="utf-8")
    config = runner.load_config(env)
    plan = runner.dry_run_plan(
        config,
        job_id=7,
        repo="yzhlx/hermes-open-swe-smoke-test",
        base="main",
        delivery_id="phase2-pi-synthetic-delivery",
    )
    serialized = json.dumps(plan, sort_keys=True)
    assert plan["github_writes"] is False
    assert plan["cloud_writes"] is False
    assert plan["provider_calls"] is False
    assert plan["runner"] == "HostAgentJobRunner"
    assert plan["agent"] == "PiCliRunner"
    assert plan["sandbox"] == "HermesDockerSandboxBackend"
    assert plan["tools"] == [
        "read", "write", "edit", "ls", "find", "grep", "submit_result"
    ]
    assert plan["pi"]["built_in_tools"] is False
    assert plan["pi"]["session"] is False
    assert plan["pi"]["prompt_transport"] == "stdin"
    assert "echo" not in serialized.lower()
    assert "mock" not in serialized.lower()
    assert "synthetic-worker-token" not in serialized


def test_pi_preflight_requires_binary_agent_dir_extension_and_guard(tmp_path, monkeypatch):
    env = base_env(tmp_path)
    config = runner.load_config(env)
    monkeypatch.setattr(
        runner.PiCliRunner,
        "resolve_binary",
        staticmethod(lambda _binary: "C:/trusted/pi.cmd"),
    )
    assert runner.pi_preflight(config) == "C:/trusted/pi.cmd"

    config["pi_extension_path"].with_name("pi_workspace_guard.mjs").unlink()
    with pytest.raises(RuntimeError, match="pi_workspace_guard_unavailable"):
        runner.pi_preflight(config)


def test_run_job_stops_before_token_read_when_pi_preflight_fails(tmp_path, monkeypatch):
    config = runner.load_config(base_env(tmp_path))
    args = runner.build_parser().parse_args([
        "--job-id", "1",
        "--repo", "yzhlx/hermes-open-swe-smoke-test",
        "--base", "main",
        "--task", "synthetic",
        "--delivery-id", "delivery-1",
        "--test-command", "pytest -q",
    ])
    calls = []
    monkeypatch.setattr(runner, "docker_preflight", lambda: calls.append("docker"))
    monkeypatch.setattr(
        runner,
        "pi_preflight",
        lambda _config: (_ for _ in ()).throw(RuntimeError("pi_binary_unavailable")),
    )
    monkeypatch.setattr(
        runner,
        "load_token",
        lambda *_args, **_kwargs: calls.append("token"),
    )
    with pytest.raises(RuntimeError, match="pi_binary_unavailable"):
        runner.run_job(config, args)
    assert calls == ["docker"]


def test_runner_rejects_non_smoke_repository(tmp_path):
    config = runner.load_config(base_env(tmp_path))
    with pytest.raises(ValueError, match="repo_not_allowed"):
        runner.dry_run_plan(
            config,
            job_id=1,
            repo="example/other-repository",
            base="main",
            delivery_id="phase2-invalid-repo",
        )
