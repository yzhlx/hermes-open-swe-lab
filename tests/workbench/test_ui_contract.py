from __future__ import annotations

import contextlib
import http.client
import importlib.util
import json
from pathlib import Path
import threading

import hermes_workbench.runtime as workbench_runtime
from hermes_workbench.operator_api import create_server
from hermes_worker.control_plane import ControlPlane


REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = REPO_ROOT / "web"
SMOKE_REPO = "yzhlx/hermes-open-swe-smoke-test"
CHANNELS = {
    "control-room",
    "planning",
    "implementation",
    "review",
    "qa",
    "release",
    "user-action-required",
}


@contextlib.contextmanager
def _running_server(tmp_path: Path):
    cp = ControlPlane(str(tmp_path / "ui-contract.sqlite3"))
    server = create_server(
        cp,
        host="127.0.0.1",
        port=0,
        allowed_repos={SMOKE_REPO},
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _get(server, path: str):
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_workbench_static_artifacts_are_real_and_source_of_truth_safe():
    expected = {"index.html", "app.js", "tokens.css", "styles.css", "favicon.svg"}
    assert {path.name for path in WEB_ROOT.iterdir()} == expected

    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    tokens = (WEB_ROOT / "tokens.css").read_text(encoding="utf-8")
    styles = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    favicon = (WEB_ROOT / "favicon.svg").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/tokens.css">' in html
    assert '<link rel="stylesheet" href="/styles.css">' in html
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in html
    assert '<script src="/app.js" defer></script>' in html
    assert "<script>" not in html
    assert "style=" not in html
    assert 'data-ui="connection-state"' in html
    assert 'data-ui="task-list"' in html
    assert 'data-ui="event-stream"' in html
    assert 'data-ui="command-form"' in html
    assert 'data-ui="evidence-panel"' in html
    assert 'data-ui="operator-actions"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html

    for channel in CHANNELS:
        assert json.dumps(channel) in javascript
    for action in ("pause", "resume", "supplement", "reject", "approve"):
        assert json.dumps(action) in javascript
    assert 'const API_ROOT = "/api/hermes/v1"' in javascript
    for endpoint in (
        'api("/session")',
        'api("/status")',
        'api("/tasks")',
        "/events?after=",
        "/actions",
    ):
        assert endpoint in javascript

    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "innerHTML" not in javascript
    assert 'action: "merge"' not in javascript
    assert '"/merge"' not in javascript
    assert "autoMerge" not in javascript
    assert "sample agent" not in javascript.lower()
    assert "demo agent" not in javascript.lower()
    assert tokens.lstrip().startswith("/* Hallmark ·")
    assert "oklch(" in tokens
    assert "var(--color-" in styles
    assert "oklch(" not in styles
    assert "overflow-x: clip" in styles
    assert "prefers-reduced-motion: reduce" in styles
    command_dock_styles = styles.split(".command-dock {", 1)[1].split("}", 1)[0]
    assert "position: sticky" not in command_dock_styles
    assert "inset-block-end" not in command_dock_styles
    assert favicon.lstrip().startswith("<svg")
    assert "<script" not in favicon


def test_operator_runtime_serves_every_ui_asset_with_security_headers(
    tmp_path: Path,
):
    with _running_server(tmp_path) as server:
        responses = {
            path: _get(server, path)
            for path in (
                "/",
                "/app.js",
                "/tokens.css",
                "/styles.css",
                "/favicon.svg",
            )
        }
        ready = _get(server, "/readyz")

    assert all(result[0] == 200 for result in responses.values())
    assert ready[0] == 200
    assert json.loads(ready[2]) == {"ready": True, "ui_available": True}
    for _, headers, _ in responses.values():
        lowered = {name.lower(): value for name, value in headers.items()}
        assert lowered["cache-control"] == "no-store"
        assert lowered["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in lowered["content-security-policy"]


def test_package_discovery_includes_workbench_runtime():
    spec = importlib.util.find_spec("hermes_workbench")
    assert spec is not None

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"hermes_workbench*"' in pyproject
    assert '"web/favicon.svg"' in pyproject


def test_installed_runtime_resolves_packaged_web_assets(
    tmp_path: Path,
    monkeypatch,
):
    source_root = tmp_path / "site-packages"
    source_root.mkdir()
    install_prefix = tmp_path / "venv"
    packaged_web = install_prefix / "share" / "hermes-workbench" / "web"
    monkeypatch.setattr(workbench_runtime.sys, "prefix", str(install_prefix))

    config = workbench_runtime.load_config({}, source_root)

    assert config.web_root == packaged_web
