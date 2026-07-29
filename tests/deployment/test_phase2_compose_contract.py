from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "cloud" / "docker-compose.phase2.yml"
DOCKERFILE = ROOT / "deploy" / "cloud" / "Dockerfile.phase2"
DOCKERIGNORE = ROOT / "deploy" / "cloud" / "Dockerfile.phase2.dockerignore"


def test_phase2_compose_isolated_loopback_and_bounded():
    text = COMPOSE.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "127.0.0.1:18080:8080" in text
    assert "HERMES_LISTEN_HOST: 0.0.0.0" in text
    assert 'HERMES_CONTAINER_MODE: "1"' in text
    assert "0.0.0.0:18080" not in text
    assert "/var/lib/hermes-open-swe-lab-phase2:/data" in text
    assert "restart: \"no\"" in text or "restart: 'no'" in text
    assert "read_only: true" in lowered
    assert "privileged: false" in lowered
    assert "pids_limit: 128" in lowered
    assert 'cpus: "0.5"' in lowered or "cpus: '0.5'" in lowered
    assert "mem_limit: 512m" in lowered
    assert "cap_drop:" in lowered and "- all" in lowered
    assert "no-new-privileges:true" in lowered

    assert "/opt/hermes-cloud" not in text
    assert "docker.sock" not in lowered
    assert "0.0.0.0:18080" not in text
    assert "sanbox_type" not in lowered
    assert "sandbox_type" not in lowered
    assert "provider_api_key" not in lowered
    assert "open_swe_openai_api_key" not in lowered
    assert "github_app_private_key=" not in lowered


def test_phase2_dockerfile_runs_non_root_minimal_control_plane():
    text = DOCKERFILE.read_text(encoding="utf-8")
    lowered = text.lower()

    assert re.search(
        r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$",
        text,
        re.MULTILINE,
    )
    normalized = " ".join(text.split())
    assert (
        'pip install --no-cache-dir "PyJWT[crypto]==2.10.1"'
        in normalized
    )
    assert (
        'pip install --no-cache-dir "PyJWT[crypto]==2.10.1" .'
        not in normalized
    )
    assert "user 10001:10001" in lowered
    assert "python" in lowered
    assert "-m" in lowered
    assert "deploy.cloud.control_plane_app" in lowered
    assert "healthcheck" in lowered
    assert "/healthz" in lowered

    assert "docker" not in "\n".join(
        line for line in lowered.splitlines()
        if not line.startswith("#") and not line.startswith("from")
    )
    assert "codex" not in lowered
    assert ".env" not in lowered
    assert "copy . " not in lowered
    assert "add . " not in lowered


def test_phase2_build_context_is_allowlisted_and_secret_safe():
    lines = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines[0] == "**"
    for required in (
        "!pyproject.toml",
        "!README.md",
        "!hermes_worker/**",
        "!hermes_open_swe_relay/**",
        "!deploy/**",
        "**/.git/**",
        "**/.env.*",
        "**/*.pem",
        "**/*.key",
        "**/secrets/**",
        "**/credentials/**",
        "**/.pi-subagents/**",
        "**/runtime/**",
        "docs/authorization/**",
        "CODEX-FINAL-HANDOFF.md",
    ):
        assert required in lines

    last_include = max(
        index for index, line in enumerate(lines) if line.startswith("!")
    )
    for index, line in enumerate(lines):
        if line in {
            "**/.env.*",
            "**/*.pem",
            "**/*.key",
            "**/secrets/**",
            "**/credentials/**",
        }:
            assert index > last_include


def test_compose_uses_separate_secret_files_by_name_only():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "HERMES_WORKER_TOKEN_HASHES_FILE" in text
    assert "HERMES_HOST_WORKER_TOKEN_HASHES_FILE" in text
    assert "HERMES_HOST_WORKER_TOKEN_HASHES_SOURCE" in text
    assert "HERMES_GITHUB_APP_ID_FILE" in text
    assert "HERMES_GITHUB_INSTALLATION_ID_FILE" in text
    assert "HERMES_GITHUB_APP_PRIVATE_KEY_FILE" in text
    assert "/run/secrets/" in text
    assert "ADMIN_INIT_TOKEN" not in text
    assert "HERMES_SECRET_KEY" not in text
