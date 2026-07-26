"""Offline / safe-environment tests for the local Worker runner (line C).

Validates the deployment hardening: token-file permission gate, Docker
preflight failure, and the cloud-outage exponential-backoff + auto-recovery
loop. No Docker, no real cloud, no secrets involved.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
import time

from conftest import WORKTREE, start_control_plane, wait_for_health, free_port

import deploy.worker.worker_runner as wm
from hermes_worker.worker import HermesWorker
from hermes_worker.echo_sandbox import EchoSandboxBackend


# --------------------------------------------------------------------------
# Token file: restricted-permission gate
# --------------------------------------------------------------------------
class _FakeStat:
    def __init__(self, mode: int):
        self.st_mode = mode


def test_token_file_missing():
    try:
        wm.load_token("/nonexistent/worker_token", strict=True)
        assert False, "missing token file must fatal"
    except SystemExit:
        pass


def test_token_file_perm_too_open(monkeypatch):
    monkeypatch.setattr(wm.os, "stat", lambda p: _FakeStat(0o644))
    monkeypatch.setattr(wm.os.path, "exists", lambda p: True)
    try:
        wm.load_token("/x/worker_token", strict=True)
        assert False, "group/other-accessible token file must be refused"
    except SystemExit:
        pass


def test_token_file_perm_ok(monkeypatch, tmp_path):
    f = tmp_path / "tok"
    f.write_text("secret-token")
    monkeypatch.setattr(wm.os, "stat", lambda p: _FakeStat(0o600))
    tok = wm.load_token(str(f), strict=True)
    assert tok == "secret-token"


# --------------------------------------------------------------------------
# Docker preflight: clear failure when daemon unavailable
# --------------------------------------------------------------------------
def test_docker_preflight_missing_binary(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(wm.subprocess, "run", _raise)
    try:
        wm.docker_preflight()
        assert False, "missing docker binary must fatal"
    except SystemExit:
        pass


def test_docker_preflight_daemon_down(monkeypatch):
    class _R:
        returncode = 1
    monkeypatch.setattr(wm.subprocess, "run", lambda *a, **k: _R())
    try:
        wm.docker_preflight()
        assert False, "unreachable docker daemon must fatal"
    except SystemExit:
        pass


# --------------------------------------------------------------------------
# Endpoint validation: HTTPS-only in production
# --------------------------------------------------------------------------
def test_validate_endpoint_production_requires_https():
    try:
        wm.validate_endpoint("http://cloud.example.com", localhost_test=False)
        assert False
    except SystemExit:
        pass
    # https is accepted
    wm.validate_endpoint("https://cloud.example.com", localhost_test=False)
    # localhost plaintext allowed only in test mode
    wm.validate_endpoint("http://127.0.0.1:8080", localhost_test=True)
    try:
        wm.validate_endpoint("http://cloud.example.com", localhost_test=True)
        assert False
    except SystemExit:
        pass


# --------------------------------------------------------------------------
# Integration: register -> survive cloud outage (backoff) -> recover
# --------------------------------------------------------------------------
def test_worker_reconnect_backoff(tmp_path):
    port = free_port()
    db = str(tmp_path / "events.db")
    token_file = tmp_path / "token"
    token_file.write_text("test-worker-token")

    app = start_control_plane(port, db, localhost_test=True)
    assert wait_for_health(port, 15), "control plane did not start"

    stop = threading.Event()
    worker = HermesWorker(f"http://127.0.0.1:{port}", "test-worker-token",
                          backend=EchoSandboxBackend(), insecure_local_ok=True)

    def last_hb():
        import sqlite3
        try:
            c = sqlite3.connect(db)
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT last_heartbeat FROM workers").fetchone()
            c.close()
            return row["last_heartbeat"] if row else None
        except sqlite3.OperationalError:
            # DB not yet initialized (lazy create on first request).
            return None

    t = threading.Thread(target=wm.run_loop, args=(worker, stop),
                         kwargs={"poll_interval": 0.3, "base": 0.2, "cap": 1.0},
                         daemon=True)
    t.start()

    # Wait for registration + a heartbeat while cloud is up.
    deadline = time.time() + 15
    while last_hb() is None and time.time() < deadline:
        time.sleep(0.1)
    assert last_hb() is not None, "worker never registered"
    t_up = last_hb()

    # Simulate cloud outage.
    app.terminate(); app.wait(timeout=10)
    time.sleep(1.5)  # let the worker hit its backoff path
    assert t.is_alive(), "worker must survive cloud outage (backoff, not crash)"

    # Restore the cloud.
    app2 = start_control_plane(port, db, localhost_test=True)
    assert wait_for_health(port, 15), "control plane did not restart"

    # Worker must recover: heartbeat timestamp advances past t_up.
    deadline = time.time() + 15
    recovered = False
    while time.time() < deadline:
        hb = last_hb()
        if hb is not None and hb > t_up:
            recovered = True
            break
        time.sleep(0.2)
    assert recovered, "worker did not recover after cloud came back"

    stop.set(); t.join(timeout=5)
    app2.terminate(); app2.wait(timeout=10)
