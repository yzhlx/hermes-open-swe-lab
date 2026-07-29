"""Stable loopback runtime for the Hermes operator workbench."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Mapping

from hermes_worker.control_plane import ControlPlane

from .operator_api import (
    DEFAULT_ALLOWED_REPOS,
    LOOPBACK_HOSTS,
    create_server,
)


@dataclass(frozen=True)
class RuntimeConfig:
    host: str
    port: int
    db_path: Path
    web_root: Path


def _path_value(raw: str | None, default: Path, repo_root: Path) -> Path:
    value = Path(raw) if raw else default
    return value if value.is_absolute() else repo_root / value


def _default_web_root(repo_root: Path) -> Path:
    source_web = repo_root / "web"
    if source_web.is_dir():
        return source_web
    return Path(sys.prefix) / "share" / "hermes-workbench" / "web"


def load_config(env: Mapping[str, str], repo_root) -> RuntimeConfig:
    root = Path(repo_root)
    host = env.get("HERMES_WORKBENCH_HOST", "127.0.0.1")
    if host not in LOOPBACK_HOSTS:
        raise ValueError("workbench_loopback_only")

    raw_port = env.get("HERMES_WORKBENCH_PORT", "4175")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise ValueError("invalid_workbench_port")
    if port < 1 or port > 65_535:
        raise ValueError("invalid_workbench_port")

    db_path = _path_value(
        env.get("HERMES_WORKBENCH_DB_PATH"),
        root / "runtime" / "events.db",
        root,
    )
    web_root = _path_value(
        env.get("HERMES_WORKBENCH_WEB_ROOT"),
        _default_web_root(root),
        root,
    )
    return RuntimeConfig(
        host=host,
        port=port,
        db_path=db_path,
        web_root=web_root,
    )


def create_server_from_config(
    config: RuntimeConfig,
    *,
    allowed_repos=DEFAULT_ALLOWED_REPOS,
):
    control_plane = ControlPlane(str(config.db_path))
    try:
        server = create_server(
            control_plane,
            host=config.host,
            port=config.port,
            allowed_repos=allowed_repos,
            web_root=config.web_root,
        )
    except Exception:
        control_plane.conn.close()
        raise

    original_server_close = server.server_close
    closed = False

    def close_runtime_server() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            original_server_close()
        finally:
            control_plane.conn.close()

    server.server_close = close_runtime_server
    server.runtime_config = config
    return server
