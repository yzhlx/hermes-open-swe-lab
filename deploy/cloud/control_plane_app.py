"""Separate Hermes Open SWE Lab cloud ControlPlane entry point."""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import sys
import threading

from hermes_worker.github_app import (
    GitHubAppTokenBroker,
    RealAppApiClient,
)
from hermes_worker.worker_api_server import run_server as run_worker_server


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    return {
        "db_path": _env("HERMES_DB_PATH", "runtime/events.db"),
        "host": _env("HERMES_LISTEN_HOST", "127.0.0.1"),
        "port": int(_env("HERMES_LISTEN_PORT", "8080")),
        "localhost_test": _env("HERMES_LOCALHOST_TEST", "0") == "1",
        "container_mode": _env("HERMES_CONTAINER_MODE", "0") == "1",
        "log_dir": _env("HERMES_LOG_DIR", "logs"),
        "worker_hashes_file": _env("HERMES_WORKER_TOKEN_HASHES_FILE"),
        "host_worker_hashes_file": _env(
            "HERMES_HOST_WORKER_TOKEN_HASHES_FILE"
        ),
        "app_id_file": _env("HERMES_GITHUB_APP_ID_FILE"),
        "installation_id_file": _env(
            "HERMES_GITHUB_INSTALLATION_ID_FILE"
        ),
        "private_key_file": _env(
            "HERMES_GITHUB_APP_PRIVATE_KEY_FILE"
        ),
    }


def guard_startup(cfg: dict) -> None:
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            print(
                "SECURITY: refusing to start Control Plane as root",
                file=sys.stderr,
            )
            raise SystemExit(2)
    except AttributeError:
        pass
    container_mode = bool(cfg.get("container_mode", False))
    if cfg["host"] not in LOOPBACK_HOSTS and not (
        container_mode and cfg["host"] == "0.0.0.0"
    ):
        print(
            "SECURITY: refusing unsafe listen host",
            file=sys.stderr,
        )
        raise SystemExit(3)
    if int(cfg["port"]) < 1 or int(cfg["port"]) > 65_535:
        print("SECURITY: invalid listen port", file=sys.stderr)
        raise SystemExit(4)


def _read_required(path_value: str, label: str) -> str:
    if not path_value:
        raise RuntimeError(f"missing_{label}_file")
    path = Path(path_value)
    if not path.is_file():
        raise RuntimeError(f"missing_{label}_file")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"empty_{label}_file")
    return value


def _load_worker_hashes(path_value: str) -> set[str]:
    content = _read_required(path_value, "worker_hashes")
    hashes = {
        line.strip().lower()
        for line in content.splitlines()
        if line.strip()
    }
    if not hashes or any(not _SHA256_RE.fullmatch(value) for value in hashes):
        raise RuntimeError("invalid_worker_hashes_file")
    return hashes


def _build_broker(cfg: dict) -> GitHubAppTokenBroker:
    app_id = _read_required(cfg["app_id_file"], "github_app_id")
    installation_id = _read_required(
        cfg["installation_id_file"],
        "github_installation_id",
    )
    private_key = _read_required(
        cfg["private_key_file"],
        "github_private_key",
    )
    if "PRIVATE KEY" not in private_key:
        raise RuntimeError("invalid_github_private_key_file")
    return GitHubAppTokenBroker(
        app_id=app_id,
        installation_id=installation_id,
        private_key_pem=private_key,
        app_api=RealAppApiClient(),
    )


def run_server(cfg: dict):
    worker_hashes = (
        _load_worker_hashes(cfg["worker_hashes_file"])
        if cfg.get("worker_hashes_file") else None
    )
    host_worker_hashes = (
        _load_worker_hashes(cfg["host_worker_hashes_file"])
        if cfg.get("host_worker_hashes_file") else None
    )
    if host_worker_hashes and (
        not worker_hashes
        or not host_worker_hashes.issubset(worker_hashes)
    ):
        raise RuntimeError("host_worker_hash_not_allowlisted")

    broker_files = (
        cfg.get("app_id_file"),
        cfg.get("installation_id_file"),
        cfg.get("private_key_file"),
    )
    broker = _build_broker(cfg) if all(broker_files) else None
    if not cfg.get("localhost_test", False):
        if not worker_hashes:
            raise RuntimeError("missing_worker_hashes_file")
        if not host_worker_hashes:
            raise RuntimeError("missing_host_worker_hashes_file")
        if broker is None:
            raise RuntimeError("missing_github_broker_files")
    return run_worker_server(
        cfg["host"],
        cfg["port"],
        cfg["db_path"],
        allowed_token_hashes=worker_hashes,
        host_worker_token_hashes=host_worker_hashes,
        broker=broker,
        health_endpoints=True,
    )


def main() -> None:
    cfg = load_config()
    guard_startup(cfg)

    runtime = Path(cfg["db_path"]).parent
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        runtime.chmod(0o750)
    except OSError:
        pass

    server = run_server(cfg)
    stop = threading.Event()

    def handle_signal(_signum, _frame):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, AttributeError):
            pass

    print(
        f"Hermes Control Plane listening on {cfg['host']}:{cfg['port']}",
        flush=True,
    )
    server.serve_forever()
    print("Hermes Control Plane stopped.", flush=True)


if __name__ == "__main__":
    main()
