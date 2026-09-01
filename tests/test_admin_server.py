from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deribit_engine.admin_server.app import assert_admin_bind_host, create_admin_app
from deribit_engine.admin_server.catalog import build_admin_catalog, probe_frontend_health
from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_frontend_launchd import FrontendLaunchdResult


def _write_registry(repo_root: Path) -> None:
    (repo_root / "deribit_engine").mkdir(exist_ok=True)
    registry_dir = repo_root / "config" / "platform"
    registry_dir.mkdir(parents=True)
    (repo_root / "config" / "investors").mkdir(parents=True)
    (registry_dir / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{repo_root}"',
                'domain = "portfolio.test"',
                "next_frontend_port = 8800",
                "",
                "[[investors]]",
                'id = "alice"',
                'display_name = "Alice"',
                'dashboard_email = "alice@example.com"',
                'access_method = "email"',
                'hostname = "alice.portfolio.test"',
                "frontend_port = 8765",
                "live_enabled = true",
                "frontend_enabled = true",
                "",
                "[[investors]]",
                'id = "bob"',
                'display_name = "Bob"',
                'dashboard_email = ""',
                'access_method = "email"',
                'hostname = "bob.portfolio.test"',
                "frontend_port = 8766",
                "live_enabled = false",
                "frontend_enabled = false",
            ]
        ),
        encoding="utf-8",
    )


class _FakeResponse:
    status = 200

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _size: int = -1) -> bytes:
        return b'{"ok": true}'


def test_assert_admin_bind_host_defaults_to_loopback() -> None:
    assert_admin_bind_host("127.0.0.1", allow_public=False)
    assert_admin_bind_host("localhost", allow_public=False)
    with pytest.raises(ConfigurationError, match="refuses to bind"):
        assert_admin_bind_host("0.0.0.0", allow_public=False)
    assert_admin_bind_host("0.0.0.0", allow_public=True)


def test_probe_frontend_health_missing_port() -> None:
    result = probe_frontend_health(None)
    assert result["ok"] is False
    assert result["error"] == "missing frontend_port"


def test_build_admin_catalog_without_probe(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    catalog = build_admin_catalog(repo_root=tmp_path, probe=False)
    assert catalog["investor_count"] == 2
    assert catalog["healthy_count"] == 0
    alice = catalog["investors"][0]
    assert alice["investor_id"] == "alice"
    assert alice["ops_url"] == "http://127.0.0.1:8765/index.html"
    assert alice["portal_url"] == "http://127.0.0.1:8765/investor.html"
    assert alice["health"]["error"] == "probe_disabled"


def test_build_admin_catalog_probes_health(tmp_path: Path, monkeypatch) -> None:
    _write_registry(tmp_path)
    seen: list[str] = []

    def fake_urlopen(request, *, timeout: float):
        del timeout
        seen.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr("deribit_engine.admin_server.catalog.urllib.request.urlopen", fake_urlopen)
    catalog = build_admin_catalog(repo_root=tmp_path, probe=True)
    assert catalog["healthy_count"] == 2
    assert "http://127.0.0.1:8765/api/health" in seen
    assert "http://127.0.0.1:8766/api/health" in seen


def test_admin_app_lists_investors_and_serves_page(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    client = TestClient(create_admin_app(repo_root=tmp_path))

    health = client.get("/api/admin/health")
    assert health.status_code == 200
    assert health.json()["role"] == "admin"

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"] == "/admin.html"

    page = client.get("/admin.html")
    assert page.status_code == 200
    assert "Admin" in page.text
    assert "/src/admin.js" in page.text
    assert "admin-workspace" in page.text
    assert "Panic close" in page.text
    index = client.get("/index.html", follow_redirects=False)
    assert index.status_code == 302
    script = client.get("/src/admin.js")
    assert script.status_code == 200
    assert "api/admin/investors" in script.text

    catalog = client.get("/api/admin/investors?probe=false")
    assert catalog.status_code == 200
    payload = catalog.json()
    assert [row["investor_id"] for row in payload["investors"]] == ["alice", "bob"]


def test_admin_app_rejects_non_loopback_host(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    client = TestClient(create_admin_app(repo_root=tmp_path))
    response = client.get("/api/admin/health", headers={"host": "admin.debopt.com"})
    assert response.status_code == 403


def test_admin_frontend_action_uses_launchd_helper(tmp_path: Path, monkeypatch) -> None:
    _write_registry(tmp_path)
    captured: dict[str, Any] = {}

    def fake_manage(action, **kwargs):
        captured["action"] = action
        captured["kwargs"] = kwargs
        return [
            FrontendLaunchdResult(
                investor_id="alice",
                label="com.deribit.frontend.alice",
                frontend_port=8765,
                action=action,
                ok=True,
                state="healthy",
                message="launchd loaded; /api/health OK",
                health_ok=True,
            )
        ]

    monkeypatch.setattr(
        "deribit_engine.investor_frontend_launchd.manage_frontend_launchd",
        fake_manage,
    )
    client = TestClient(create_admin_app(repo_root=tmp_path))
    response = client.post("/api/admin/frontend/alice/restart")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "restart"
    assert captured["action"] == "restart"
    assert captured["kwargs"]["investor_id"] == "alice"
    assert captured["kwargs"]["include_disabled"] is True


def test_admin_frontend_action_rejects_unknown(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    client = TestClient(create_admin_app(repo_root=tmp_path))
    response = client.post("/api/admin/frontend/alice/explode")
    assert response.status_code == 400
