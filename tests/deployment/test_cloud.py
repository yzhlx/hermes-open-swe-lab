"""Offline / safe-environment tests for the cloud Control Plane deployment.

These run locally (Git Bash + managed Python) without touching the cloud host,
real GitHub, or any secret. They validate the deployment *assets*: config check,
systemd unit structure, app security gates, health endpoints, restart, SQLite
backup/restore, log rotation, deploy idempotency, and rollback.
"""
from __future__ import annotations

import getpass
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

from conftest import WORKTREE, run_bash, start_control_plane, wait_for_health, \
    build_git_repo, commit, free_port

import deploy.cloud.control_plane_app as cpa
from hermes_worker import db as hermes_db


def _current_user():
    import subprocess as _sp
    r = _sp.run(["id", "-un"], capture_output=True, text=True)
    return r.stdout.strip() or getpass.getuser()


def _posix(p):
    return str(p).replace("\\", "/")


# --------------------------------------------------------------------------
# check_config.sh (fail-closed)
# --------------------------------------------------------------------------
def _required_env(extra=None):
    e = {
        "HERMES_DB_PATH": "runtime/events.db",
        "HERMES_RUNTIME_DIR": "runtime",
        "HERMES_LOG_DIR": "logs",
        "HERMES_LISTEN_HOST": "127.0.0.1",
        "HERMES_LISTEN_PORT": "8080",
    }
    if extra:
        e.update(extra)
    return e


def test_check_config_missing_vars():
    rc, _ = run_bash("scripts/check_config.sh", {"HERMES_LISTEN_HOST": "127.0.0.1"})
    assert rc != 0, "missing required vars must fail closed"


def test_check_config_secret_in_env():
    rc, _ = run_bash("scripts/check_config.sh",
                     _required_env({"HERMES_API_KEY": "sk-xxxx"}))
    assert rc != 0, "secret in env file must be rejected"


def test_check_config_non_loopback():
    rc, _ = run_bash("scripts/check_config.sh",
                     _required_env({"HERMES_LISTEN_HOST": "0.0.0.0"}))
    assert rc != 0, "non-loopback bind must be rejected"


def test_check_config_wrong_user():
    rc, _ = run_bash("scripts/check_config.sh",
                     _required_env({"HERMES_SERVICE_USER": "hermes-swe"}))
    assert rc != 0, "non-hermes-swe identity must be rejected"


def test_check_config_ok():
    rc, _ = run_bash("scripts/check_config.sh",
                     _required_env({"HERMES_SERVICE_USER": _current_user()}))
    assert rc == 0, "valid config must pass"


# --------------------------------------------------------------------------
# systemd unit structure (offline stand-in for `systemd-analyze verify`)
# --------------------------------------------------------------------------
def test_systemd_unit_structure():
    text = (WORKTREE / "systemd" / "hermes-swe-control-plane.service").read_text()
    assert "[Unit]" in text and "[Service]" in text and "[Install]" in text
    assert "User=hermes-swe" in text
    assert "Restart=on-failure" in text
    assert "ExecStartPre=" in text and "check_config.sh" in text
    assert "EnvironmentFile=" in text
    # Belt-and-suspenders: no public bind literal anywhere in the unit.
    assert "0.0.0.0" not in text
    # Hardening present.
    assert "NoNewPrivileges=true" in text


# --------------------------------------------------------------------------
# App security gates (deterministic via monkeypatch)
# --------------------------------------------------------------------------
def test_app_refuses_root(monkeypatch):
    monkeypatch.setattr(cpa.os, "geteuid", lambda: 0, raising=False)
    with __import__("pytest").raises(SystemExit) as e:
        cpa.guard_startup({"host": "127.0.0.1", "port": 8080,
                            "localhost_test": True, "log_dir": "logs",
                            "db_path": "runtime/events.db"})
    assert e.value.code == 2


def test_app_refuses_non_loopback(monkeypatch):
    monkeypatch.setattr(cpa.os, "geteuid", lambda: 1, raising=False)
    with __import__("pytest").raises(SystemExit) as e:
        cpa.guard_startup({"host": "0.0.0.0", "port": 8080,
                            "localhost_test": True, "log_dir": "logs",
                            "db_path": "runtime/events.db"})
    assert e.value.code == 3


def test_production_host_worker_hashes_must_be_allowlisted_subset(
    tmp_path,
    monkeypatch,
):
    all_workers = tmp_path / "all-worker-hashes"
    host_workers = tmp_path / "host-worker-hashes"
    all_workers.write_text("a" * 64 + "\n" + "b" * 64 + "\n")
    host_workers.write_text("b" * 64 + "\n")
    cfg = {
        "host": "127.0.0.1",
        "port": 8080,
        "db_path": str(tmp_path / "events.db"),
        "localhost_test": False,
        "worker_hashes_file": str(all_workers),
        "host_worker_hashes_file": str(host_workers),
        "app_id_file": "synthetic-app-id-file",
        "installation_id_file": "synthetic-installation-id-file",
        "private_key_file": "synthetic-private-key-file",
    }
    captured = {}
    marker = object()
    monkeypatch.setattr(cpa, "_build_broker", lambda _cfg: object())

    def fake_server(*args, **kwargs):
        captured.update(kwargs)
        return marker

    monkeypatch.setattr(cpa, "run_worker_server", fake_server)
    assert cpa.run_server(cfg) is marker
    assert captured["allowed_token_hashes"] == {"a" * 64, "b" * 64}
    assert captured["host_worker_token_hashes"] == {"b" * 64}

    host_workers.write_text("c" * 64 + "\n")
    with __import__("pytest").raises(
        RuntimeError,
        match="host_worker_hash_not_allowlisted",
    ):
        cpa.run_server(cfg)


def test_app_allows_wildcard_only_inside_explicit_container_mode(monkeypatch):
    monkeypatch.setattr(cpa.os, "geteuid", lambda: 1, raising=False)
    safe_container = {
        "host": "0.0.0.0",
        "port": 8080,
        "container_mode": True,
    }
    cpa.guard_startup(safe_container)

    for cfg in (
        {"host": "0.0.0.0", "port": 8080, "container_mode": False},
        {"host": "192.0.2.10", "port": 8080, "container_mode": True},
    ):
        with __import__("pytest").raises(SystemExit) as error:
            cpa.guard_startup(cfg)
        assert error.value.code == 3


# --------------------------------------------------------------------------
# Health + restart (real subprocess)
# --------------------------------------------------------------------------
def test_healthz_readyz():
    port = free_port()
    db = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"hermes-hc-{port}.db")
    if os.path.exists(db):
        os.remove(db)
    p = start_control_plane(port, db)
    try:
        assert wait_for_health(port, 15)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",
                                    timeout=2) as r:
            assert r.status == 200
    finally:
        p.terminate()
        p.wait(timeout=10)


def test_service_restart():
    port = free_port()
    db = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"hermes-rs-{port}.db")
    if os.path.exists(db):
        os.remove(db)
    p1 = start_control_plane(port, db)
    try:
        assert wait_for_health(port, 15)
    finally:
        p1.terminate(); p1.wait(timeout=10)
    # Restart on the same port (proves clean shutdown + rebind).
    p2 = start_control_plane(port, db)
    try:
        assert wait_for_health(port, 15)
    finally:
        p2.terminate(); p2.wait(timeout=10)


# --------------------------------------------------------------------------
# SQLite backup / restore
# --------------------------------------------------------------------------
def test_sqlite_backup_restore(tmp_path):
    src = str(tmp_path / "events.db")
    conn = hermes_db.init_db(src)
    conn.execute("INSERT INTO jobs(task_id, state) VALUES (?,?)", ("t1", "pending"))
    conn.commit(); conn.close()

    bk_dir = tmp_path / "backups"
    r = subprocess.run([sys.executable, "scripts/backup_sqlite.py",
                        "--src", src, "--dst-dir", str(bk_dir)],
                       cwd=str(WORKTREE), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    backups = list(bk_dir.glob("events-*.db"))
    assert backups, "backup file expected"

    restore_target = str(tmp_path / "restored.db")
    # Without --force on an existing target -> refused (fail-closed).
    open(restore_target, "w").close()
    r2 = subprocess.run([sys.executable, "scripts/restore_sqlite.py",
                         "--backup", str(backups[0]), "--target", restore_target],
                        cwd=str(WORKTREE), capture_output=True, text=True)
    assert r2.returncode == 2, "restore must require --force over existing target"

    r3 = subprocess.run([sys.executable, "scripts/restore_sqlite.py",
                         "--backup", str(backups[0]), "--target", restore_target,
                         "--force"],
                        cwd=str(WORKTREE), capture_output=True, text=True)
    assert r3.returncode == 0, r3.stderr
    rc = sqlite3.connect(restore_target)
    n = rc.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rc.close()
    assert n == 1


# --------------------------------------------------------------------------
# Log rotation
# --------------------------------------------------------------------------
def test_log_rotation(tmp_path):
    logdir = tmp_path / "logs"
    logdir.mkdir()
    big = logdir / "run.jsonl"
    big.write_text("x" * 100)  # exceeds tiny threshold
    r = subprocess.run([sys.executable, "scripts/rotate_logs.py", str(logdir),
                        "--keep", "2", "--max-bytes", "10"],
                       cwd=str(WORKTREE), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    gz = list(logdir.glob("run.jsonl.*.gz"))
    assert gz, "expected a rotated .gz"
    assert not big.exists(), "original should be rotated away"

    # Pruning: drop the existing .gz, then create several *.jsonl files that
    # exceed the threshold; rotation should keep only `keep` (2) gz files.
    for g in logdir.glob("*.gz"):
        g.unlink()
    for i in range(5):
        (logdir / f"r{i}.jsonl").write_text("y" * 100)
    subprocess.run([sys.executable, "scripts/rotate_logs.py", str(logdir),
                    "--keep", "2", "--max-bytes", "10"],
                   cwd=str(WORKTREE), capture_output=True)
    assert len(list(logdir.glob("*.jsonl.*.gz"))) == 2


# --------------------------------------------------------------------------
# Deploy idempotency + rollback
# --------------------------------------------------------------------------
def test_deploy_idempotency(tmp_path):
    repo = build_git_repo(tmp_path)
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                           capture_output=True, text=True).stdout.strip()
    app_home = tmp_path / "install"
    env = dict(os.environ)
    env.update({
        "HERMES_APP_HOME": _posix(app_home),
        "HERMES_DEPLOY_SRC": _posix(repo),
        "HERMES_SERVICE_USER": _current_user(),
        "HERMES_AUTOSTART": "0",
        "HERMES_DB_PATH": _posix(app_home / "runtime" / "events.db"),
        "HERMES_RUNTIME_DIR": _posix(app_home / "runtime"),
        "HERMES_LOG_DIR": _posix(app_home / "logs"),
        "HERMES_LISTEN_HOST": "127.0.0.1",
        "HERMES_LISTEN_PORT": "8080",
        "PYTHONPATH": _posix(WORKTREE),
    })
    rc1, out1 = run_bash("scripts/deploy_control_plane.sh", env)
    assert rc1 == 0, out1
    rc2, out2 = run_bash("scripts/deploy_control_plane.sh", env)
    assert rc2 == 0, out2
    assert (app_home / "deploy" / "cloud" / "control_plane_app.py").exists()
    assert (app_home / "DEPLOYED_SHA").read_text().strip() == sha_a


def test_rollback(tmp_path):
    repo = build_git_repo(tmp_path)
    (repo / "deploy" / "DEPLOY_MARKER").write_text("A")
    sha_a = commit(repo, "marker A")
    (repo / "deploy" / "DEPLOY_MARKER").write_text("B")
    sha_b = commit(repo, "marker B")

    subprocess.run(["git", "checkout", "-q", sha_a], cwd=str(repo), check=True)
    app_home = tmp_path / "installB"
    env = dict(os.environ)
    env.update({
        "HERMES_APP_HOME": _posix(app_home),
        "HERMES_DEPLOY_SRC": _posix(repo),
        "HERMES_SERVICE_USER": _current_user(),
        "HERMES_AUTOSTART": "0",
        "HERMES_DB_PATH": _posix(app_home / "runtime" / "events.db"),
        "HERMES_RUNTIME_DIR": _posix(app_home / "runtime"),
        "HERMES_LOG_DIR": _posix(app_home / "logs"),
        "HERMES_LISTEN_HOST": "127.0.0.1",
        "HERMES_LISTEN_PORT": "8080",
        "PYTHONPATH": _posix(WORKTREE),
    })
    rc, _ = run_bash("scripts/deploy_control_plane.sh", env)
    assert rc == 0
    assert (app_home / "deploy" / "DEPLOY_MARKER").read_text() == "A"

    subprocess.run(["git", "checkout", "-q", sha_b], cwd=str(repo), check=True)
    rc, _ = run_bash("scripts/deploy_control_plane.sh", env)
    assert rc == 0
    assert (app_home / "deploy" / "DEPLOY_MARKER").read_text() == "B"

    rc, out = run_bash("scripts/rollback_control_plane.sh", env)
    assert rc == 0, out
    assert (app_home / "deploy" / "DEPLOY_MARKER").read_text() == "A"
