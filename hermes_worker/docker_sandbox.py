"""Hermes Docker Sandbox Backend — IMPLEMENTED IN PHASE D2.

This module defines the contract for the real, Docker-based sandbox backend
that conforms to the ``SandboxBackend`` protocol. It is intentionally NOT
implemented in D1: D1 uses ``EchoSandboxBackend`` for offline protocol tests.

Design (Phase D2), per the architecture decision:
- Runs ``docker run`` with the MVP limits:
    cpus=1, memory=2GB, pids_limit=256, timeout=20min, concurrency=1,
    privileged=false, host_network=false, docker_socket_mount=forbidden,
    auto_remove=true.
- Mounts ONLY the current task's temporary working directory.
- Never mounts: user home, SSH keys, browser cookies, Hermes prod config,
  other GitHub repos, the Docker host socket, or Windows system dirs.
- Uses a short-lived GitHub App Installation Token scoped to
  ``yzhlx/hermes-open-swe-smoke-test`` only.
- Follow-up messages reuse the same container/workspace; auto-destroys on
  timeout; recoverable after worker disconnect (job re-queued, new container).
"""
from __future__ import annotations

from .protocol import SandboxBackend


class HermesDockerSandboxBackend(SandboxBackend):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "HermesDockerSandboxBackend is implemented in Phase D2. "
            "D1 uses EchoSandboxBackend for offline protocol testing."
        )
