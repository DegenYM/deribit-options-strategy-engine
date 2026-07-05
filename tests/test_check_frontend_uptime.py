from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Any

import scripts.check_frontend_uptime as uptime


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


def _write_registry(repo_root: Path) -> None:
    registry_dir = repo_root / "config" / "platform"
    registry_dir.mkdir(parents=True)
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
                "live_enabled = true",
                "frontend_enabled = false",
            ]
        ),
        encoding="utf-8",
    )


def test_load_frontend_targets_uses_registry_enabled_rows(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    targets = uptime.load_frontend_targets(tmp_path)

    assert [target.investor_id for target in targets] == ["alice"]
    assert targets[0].hostname == "alice.portfolio.test"
    assert targets[0].frontend_port == 8765
    assert targets[0].url == "http://127.0.0.1:8765/api/health"


def test_main_success_checks_health_endpoint(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_registry(tmp_path)
    seen: dict[str, Any] = {}

    def fake_urlopen(request, *, timeout: float):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(uptime, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(uptime.urllib.request, "urlopen", fake_urlopen)

    code = uptime.main(["--dry-run", "--timeout", "1.5", "--json"])

    assert code == 0
    assert seen == {"url": "http://127.0.0.1:8765/api/health", "timeout": 1.5}
    assert '"failure_count": 0' in capsys.readouterr().out


def test_main_failure_sends_telegram_alert(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_registry(tmp_path)
    sent: list[tuple[str, str, str]] = []

    def fake_urlopen(_request, *, timeout: float):
        raise urllib.error.URLError(f"refused after {timeout}s")

    def fake_format_alert_message(**kwargs):
        return f"{kwargs['title']}|{kwargs['investor_id']}|{kwargs['body']}"

    def fake_send_telegram_alert(message: str, *, event_key: str, level: str) -> bool:
        sent.append((message, event_key, level))
        return True

    monkeypatch.setattr(uptime, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(uptime.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("deribit_engine.telegram_alerts.bootstrap_telegram_env", lambda _repo_root: None)
    monkeypatch.setattr("deribit_engine.telegram_alerts.format_alert_message", fake_format_alert_message)
    monkeypatch.setattr("deribit_engine.telegram_alerts.send_telegram_alert", fake_send_telegram_alert)

    code = uptime.main(["--timeout", "2"])

    assert code == 1
    assert sent == [
        (
            "Frontend uptime check failed|alice|Endpoint: /api/health\n"
            "URL: http://127.0.0.1:8765/api/health\n"
            "HTTP status: n/a\n"
            "Error: refused after 2.0s\n"
            "Registry hostname: alice.portfolio.test\n"
            "Registry port: 8765",
            "frontend_uptime:alice",
            "critical",
        )
    ]
    assert "FAIL investor=alice" in capsys.readouterr().out
