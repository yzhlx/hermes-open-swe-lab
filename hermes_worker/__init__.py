"""Hermes Open SWE MVP-0 — Cloud Control Plane + Local Worker (Phase D0/D1).

This package implements the LangSmith-free architecture decided in
ADR-002-REMOVE-LANGSMITH.md:

- SQLite task queue + Event Store            (db.py)
- Cloud-side Worker API                      (worker_api_server.py + control_plane.py)
- Local Hermes Worker (outbound HTTPS only)  (worker.py)
- Sandbox backends                           (protocol.py, echo_sandbox.py, docker_sandbox.py)

The Open SWE agent runs *through* the SandboxBackend protocol instead of being
bypassed. D1 ships the fake EchoSandboxBackend for offline protocol testing;
D2 adds the real HermesDockerSandboxBackend (Docker CLI subprocess, MVP
isolation defaults, injectable runner for offline tests).
"""

from .docker_sandbox import HermesDockerSandboxBackend  # noqa: F401
from .echo_sandbox import EchoSandboxBackend  # noqa: F401
