from __future__ import annotations

import json

import pytest

from deploy.worker import codex_worker_runner as runner


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
    token_file = tmp_path / "worker-token"
    token_file.write_text("synthetic-worker-token", encoding="utf-8")

    with pytest.raises(ValueError, match="https_required"):
        runner.load_config({
            "HERMES_CLOUD_URL": "http://cloud.example.invalid",
            "HERMES_WORKER_TOKEN_FILE": str(token_file),
        })

    config = runner.load_config({
        "HERMES_CLOUD_URL": "http://127.0.0.1:18080",
        "HERMES_LOCALHOST_TEST": "1",
        "HERMES_WORKER_TOKEN_FILE": str(token_file),
        "HERMES_CODEX_BINARY": "codex",
        "HERMES_DOCKER_IMAGE": "python:3.11-slim",
    })
    assert config["cloud_url"] == "http://127.0.0.1:18080"
    assert config["token_file"] == token_file
    assert "backend" not in config
    assert "sandbox_type" not in config


def test_dry_run_plan_is_value_free_and_performs_no_writes(tmp_path):
    token_file = tmp_path / "worker-token"
    token_file.write_text("synthetic-worker-token", encoding="utf-8")
    config = runner.load_config({
        "HERMES_CLOUD_URL": "http://127.0.0.1:18080",
        "HERMES_LOCALHOST_TEST": "1",
        "HERMES_WORKER_TOKEN_FILE": str(token_file),
        "HERMES_TASK_WORK_ROOT": str(tmp_path / "worktrees"),
    })
    plan = runner.dry_run_plan(
        config,
        job_id=7,
        repo="yzhlx/hermes-open-swe-smoke-test",
        base="main",
        delivery_id="phase2-synthetic-delivery",
    )
    serialized = json.dumps(plan, sort_keys=True)
    assert plan["github_writes"] is False
    assert plan["cloud_writes"] is False
    assert plan["runner"] == "CodexJobRunner"
    assert plan["sandbox"] == "HermesDockerSandboxBackend"
    assert "echo" not in serialized.lower()
    assert "mock" not in serialized.lower()
    assert "synthetic-worker-token" not in serialized


def test_codex_preflight_fails_before_remote_job_mutation(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(runner.Path, "is_file", lambda _path: False)
    with pytest.raises(RuntimeError, match="codex_binary_unavailable"):
        runner.codex_preflight("missing-codex")


def test_runner_rejects_non_smoke_repository(tmp_path):
    token_file = tmp_path / "worker-token"
    token_file.write_text("synthetic-worker-token", encoding="utf-8")
    config = runner.load_config({
        "HERMES_CLOUD_URL": "http://127.0.0.1:18080",
        "HERMES_LOCALHOST_TEST": "1",
        "HERMES_WORKER_TOKEN_FILE": str(token_file),
    })
    with pytest.raises(ValueError, match="repo_not_allowed"):
        runner.dry_run_plan(
            config,
            job_id=1,
            repo="example/other-repository",
            base="main",
            delivery_id="phase2-invalid-repo",
        )
