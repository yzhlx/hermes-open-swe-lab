"""Shared offline fixtures for the Hermes workbench contract tests.

These fixtures never use the network, Docker, GitHub credentials, or a live
provider.  They exercise the current ControlPlane and its SQLite event store.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermes_worker.control_plane import ControlPlane
from hermes_worker.db import hash_token


WORKER_TOKEN = "wk-workbench-contract-test"


@dataclass
class FakeClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def worker_token() -> str:
    return WORKER_TOKEN


@pytest.fixture
def control_plane(tmp_path, fake_clock, worker_token):
    cp = ControlPlane(
        str(tmp_path / "workbench-events.db"),
        now=fake_clock,
        lease_seconds=1_200,
        allowed_token_hashes={hash_token(worker_token)},
    )
    cp.register(worker_token, name="workbench-contract-worker")
    try:
        yield cp
    finally:
        cp.conn.close()


@pytest.fixture
def require_method():
    def _require(obj, name: str):
        method = getattr(obj, name, None)
        assert callable(method), (
            f"WORKBENCH_CONTRACT_MISSING: "
            f"{type(obj).__name__}.{name}() is required"
        )
        return method

    return _require
