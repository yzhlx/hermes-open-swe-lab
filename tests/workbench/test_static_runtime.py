from __future__ import annotations

import http.client
import importlib
import json
from pathlib import Path
import socket
import threading
import time
from typing import Any, Mapping

import pytest

from hermes_workbench.operator_api import create_server
from hermes_worker.control_plane import ControlPlane


SMOKE_REPO = "yzhlx/hermes-open-swe-smoke-test"


def _control_plane(db_path: Path) -> ControlPlane:
    return ControlPlane(str(db_path))


def _serve(server) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever,
        name="workbench-test-http",
        daemon=True,
    )
    thread.start()
    host, port = server.server_address[:2]
    deadline = time.monotonic() + 3
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return thread
        except OSError:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _stop(server, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def _request(
    server,
    path: str,
) -> tuple[int, Mapping[str, str], bytes]:
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            body,
        )
    finally:
        connection.close()


def _write_web_root(root: Path) -> bytes:
    root.mkdir(parents=True)
    html = (
        b"<!doctype html><html><body>"
        b"<main id='app'>Hermes Workbench</main>"
        b"</body></html>"
    )
    (root / "index.html").write_bytes(html)
    (root / "app.js").write_text(
        "document.documentElement.dataset.runtime = 'real';",
        encoding="utf-8",
    )
    (root / "tokens.css").write_text(
        ":root { --surface: #101214; }",
        encoding="utf-8",
    )
    return html


def _runtime_module():
    return importlib.import_module("hermes_workbench.runtime")


def _config_value(config: Any, name: str) -> Any:
    if isinstance(config, Mapping):
        return config[name]
    return getattr(config, name)


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_static_root_index_and_assets_have_secure_no_cache_headers(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "web"
    expected_html = _write_web_root(web_root)
    server = create_server(
        _control_plane(tmp_path / "events.sqlite3"),
        host="127.0.0.1",
        port=0,
        allowed_repos={SMOKE_REPO},
        web_root=web_root,
    )
    thread = _serve(server)
    try:
        root = _request(server, "/")
        index = _request(server, "/index.html")
        app_js = _request(server, "/app.js")
        tokens_css = _request(server, "/tokens.css")
    finally:
        _stop(server, thread)

    for status, headers, body in (root, index):
        assert status == 200
        assert body == expected_html
        assert headers["content-type"].startswith("text/html")
        assert "no-store" in headers["cache-control"].lower()
        assert headers["content-security-policy"]
        assert headers["x-content-type-options"].lower() == "nosniff"
    assert root[2] == index[2]
    assert app_js[0] == 200
    assert app_js[1]["content-type"].split(";", 1)[0] in {
        "application/javascript",
        "text/javascript",
    }
    assert tokens_css[0] == 200
    assert tokens_css[1]["content-type"].startswith("text/css")


def test_static_server_returns_404_for_unknown_directory_and_traversal(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "web"
    expected_html = _write_web_root(web_root)
    (web_root / "assets").mkdir()
    (web_root / "assets" / "index.html").write_text(
        "directory index must not be served",
        encoding="utf-8",
    )
    (tmp_path / "outside-secret.txt").write_text(
        "must-not-be-readable",
        encoding="utf-8",
    )
    server = create_server(
        _control_plane(tmp_path / "events.sqlite3"),
        host="127.0.0.1",
        port=0,
        allowed_repos={SMOKE_REPO},
        web_root=web_root,
    )
    thread = _serve(server)
    try:
        results = [
            _request(server, path)
            for path in (
                "/unknown",
                "/nested/client-route",
                "/assets/",
                "/%2e%2e/outside-secret.txt",
                "/%2e%2e%2foutside-secret.txt",
                "/%252e%252e%252foutside-secret.txt",
            )
        ]
    finally:
        _stop(server, thread)

    for status, _, body in results:
        assert status == 404
        assert body != expected_html
        assert b"must-not-be-readable" not in body
        assert b"directory index must not be served" not in body


def test_runtime_config_has_safe_defaults_validates_bind_and_allows_tmp_db(
    tmp_path: Path,
) -> None:
    runtime = _runtime_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    config = runtime.load_config({}, repo_root)
    assert _config_value(config, "host") == "127.0.0.1"
    assert _config_value(config, "port") == 4175
    assert Path(_config_value(config, "db_path")) == (
        repo_root / "runtime" / "events.db"
    )

    for unsafe_host in ("0.0.0.0", "::", "192.0.2.10"):
        with pytest.raises(ValueError):
            runtime.load_config(
                {"HERMES_WORKBENCH_HOST": unsafe_host},
                repo_root,
            )
    for invalid_port in ("0", "-1", "65536", "not-a-port"):
        with pytest.raises(ValueError):
            runtime.load_config(
                {"HERMES_WORKBENCH_PORT": invalid_port},
                repo_root,
            )

    overridden_db = tmp_path / "isolated-runtime" / "events.sqlite3"
    overridden = runtime.load_config(
        {"HERMES_WORKBENCH_DB_PATH": str(overridden_db)},
        repo_root,
    )
    assert Path(_config_value(overridden, "db_path")) == overridden_db


def test_runtime_server_reuses_sqlite_and_survives_client_disconnect(
    tmp_path: Path,
) -> None:
    runtime = _runtime_module()
    repo_root = tmp_path / "repo"
    web_root = repo_root / "web"
    _write_web_root(web_root)
    db_path = tmp_path / "runtime" / "events.sqlite3"
    db_path.parent.mkdir(parents=True)
    port = _unused_loopback_port()
    config = runtime.load_config(
        {
            "HERMES_WORKBENCH_HOST": "127.0.0.1",
            "HERMES_WORKBENCH_PORT": str(port),
            "HERMES_WORKBENCH_DB_PATH": str(db_path),
            "HERMES_WORKBENCH_WEB_ROOT": str(web_root),
        },
        repo_root,
    )

    seed = _control_plane(db_path)
    job_id, created = seed.create_job_idempotent(
        {
            "goal": "Persist a task across HTTP connections.",
            "scope": ["runtime"],
            "acceptance": ["A new client sees the same event."],
        },
        "runtime-persistence",
        repo=SMOKE_REPO,
        role="coding",
        model="test-model",
    )
    assert created is True
    seed.append_event(
        job_id,
        {
            "id": "persisted-event",
            "type": "progress",
            "payload": {"message": "Persisted in SQLite."},
        },
    )

    server = runtime.create_server_from_config(
        config,
        allowed_repos={SMOKE_REPO},
    )
    thread = _serve(server)
    event_path = f"/api/hermes/v1/tasks/{job_id}/events?after=0"
    try:
        first = _request(server, event_path)
        assert first[0] == 200
        assert b"persisted-event" in first[2]

        # _request closes its HTTPConnection. The server must remain alive and
        # a completely new connection must project the same SQLite evidence.
        assert thread.is_alive()
        second = _request(server, event_path)
        assert second[0] == 200
        assert json.loads(second[2]) == json.loads(first[2])
        assert b"persisted-event" in second[2]
    finally:
        _stop(server, thread)


def test_runtime_port_collision_raises_without_rebinding_or_killing_owner(
    tmp_path: Path,
) -> None:
    runtime = _runtime_module()
    repo_root = tmp_path / "repo"
    web_root = repo_root / "web"
    _write_web_root(web_root)
    owner = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    try:
        owner.bind(("127.0.0.1", 0))
        owner.listen(1)
        occupied_port = int(owner.getsockname()[1])
        db_path = tmp_path / "runtime" / "events.sqlite3"
        db_path.parent.mkdir(parents=True)
        config = runtime.load_config(
            {
                "HERMES_WORKBENCH_HOST": "127.0.0.1",
                "HERMES_WORKBENCH_PORT": str(occupied_port),
                "HERMES_WORKBENCH_DB_PATH": str(db_path),
                "HERMES_WORKBENCH_WEB_ROOT": str(web_root),
            },
            repo_root,
        )

        with pytest.raises(OSError):
            server = runtime.create_server_from_config(
                config,
                allowed_repos={SMOKE_REPO},
            )

        assert server is None
        assert owner.getsockname()[1] == occupied_port
        assert owner.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
    finally:
        if server is not None:
            server.server_close()
        owner.close()


def test_missing_web_root_or_index_fails_closed_but_status_remains_truthful(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing-web-root"
    root_without_index = tmp_path / "web-without-index"
    root_without_index.mkdir()

    for case_number, web_root in enumerate(
        (missing_root, root_without_index),
        start=1,
    ):
        server = create_server(
            _control_plane(tmp_path / f"events-{case_number}.sqlite3"),
            host="127.0.0.1",
            port=0,
            allowed_repos={SMOKE_REPO},
            web_root=web_root,
        )
        thread = _serve(server)
        try:
            root = _request(server, "/")
            health = _request(server, "/healthz")
            ready = _request(server, "/readyz")
            status = _request(server, "/api/hermes/v1/status")
        finally:
            _stop(server, thread)

        assert root[0] in {404, 503}
        assert root[1]["content-type"].startswith("application/json")
        assert b"<html" not in root[2].lower()
        assert health[0] == 200
        assert health[1]["content-type"].startswith("application/json")
        assert ready[0] in {200, 503}
        assert ready[1]["content-type"].startswith("application/json")
        assert status[0] == 200
        assert status[1]["content-type"].startswith("application/json")
        status_payload = json.loads(status[2])
        assert status_payload["ui_available"] is False
