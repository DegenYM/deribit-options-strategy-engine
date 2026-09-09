"""Dashboard / admin console hardening: auth gate, CORS, static allowlist, path leaks, caches."""

from __future__ import annotations

import json
import logging
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import future_expiry, make_config
from fastapi.testclient import TestClient

import deribit_engine.frontend_server as frontend_server
from deribit_engine.admin_server.app import create_admin_app
from deribit_engine.frontend_server import groups_service, helpers
from deribit_engine.frontend_server.auth import inject_token_meta, parse_cors_origins, request_presents_token
from deribit_engine.frontend_server.types import (
    EquitySnapshotScheduler,
    SingleFlightRunner,
    _TtlCache,
    make_background_executor,
)
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore
from deribit_engine.trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state

TOKEN = "s3cret-token-value"


def _make_app(tmp_path: Path, monkeypatch, *, investor_portal: bool = False, with_creds: bool = False):
    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    creds = {"client_id": "cid", "client_secret": "sec"} if with_creds else {"client_id": "", "client_secret": ""}
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", **creds)
    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    return frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
        investor_portal=investor_portal,
    )


# --------------------------------------------------------------------------- FIX 1: token gate


def test_api_token_unset_keeps_api_public(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    client = TestClient(_make_app(tmp_path, monkeypatch))
    assert client.get("/api/health").status_code == 200


def test_api_token_required_when_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    client = TestClient(_make_app(tmp_path, monkeypatch))

    denied = client.get("/api/health")
    assert denied.status_code == 401
    assert denied.headers.get("www-authenticate") == "Bearer"
    assert client.get("/api/health", headers={"Authorization": "Bearer nope"}).status_code == 401

    assert client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/health", headers={"X-Dashboard-Token": TOKEN}).status_code == 200
    # Static shell stays public so the page can load and read the token.
    assert client.get("/index.html").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_api_token_not_embedded_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    monkeypatch.delenv("DASHBOARD_API_TOKEN_EMBED", raising=False)
    client = TestClient(_make_app(tmp_path, monkeypatch))
    html = client.get("/index.html").text
    assert "dashboard-api-token" not in html
    assert TOKEN not in html


def test_api_token_embedded_when_opted_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    monkeypatch.setenv("DASHBOARD_API_TOKEN_EMBED", "true")
    client = TestClient(_make_app(tmp_path, monkeypatch, investor_portal=True))
    for page in ("/index.html", "/investor.html", "/investor.zh.html"):
        html = client.get(page).text
        assert f'<meta name="dashboard-api-token" content="{TOKEN}" />' in html


def test_inject_token_meta_escapes_and_places_in_head() -> None:
    out = inject_token_meta("<html><head></head><body></body></html>", meta_name="m", token='a"b<c')
    assert 'content="a&quot;b&lt;c"' in out
    assert out.index("<meta") < out.index("</head>")
    assert inject_token_meta("<body></body>", meta_name="m", token="t").startswith("<meta")


def test_request_presents_token_accepts_query_only_for_ws() -> None:
    scope = {"headers": [], "query_string": b"channels=market&token=" + TOKEN.encode()}
    assert request_presents_token(scope, expected=TOKEN, header_name="x-dashboard-token", allow_query=True)
    assert not request_presents_token(scope, expected=TOKEN, header_name="x-dashboard-token", allow_query=False)
    headed = {"headers": [(b"x-dashboard-token", TOKEN.encode())], "query_string": b""}
    assert request_presents_token(headed, expected=TOKEN, header_name="x-dashboard-token", allow_query=False)
    assert not request_presents_token(headed, expected="other", header_name="x-dashboard-token", allow_query=False)


@pytest.mark.enable_socket
def test_websocket_requires_token_via_query(tmp_path, monkeypatch) -> None:
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    app = _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/dashboard?channels=health"):
                pass
        with client.websocket_connect(f"/ws/dashboard?channels=health&token={TOKEN}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["channels"] == ["health"]
        with client.websocket_connect(
            "/ws/dashboard?channels=health", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as ws:
            assert ws.receive_json()["type"] == "hello"


# --------------------------------------------------------------------------- FIX 2: POST sync + CORS


def test_trade_journal_sync_is_post_only(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    calls: list[int] = []

    def _fake_run_once(self):  # noqa: ANN001
        calls.append(1)
        return {"accounts": [], "api_inserted": 0}

    monkeypatch.setattr(
        "deribit_engine.frontend_server.types.TradeJournalSyncScheduler.run_once",
        _fake_run_once,
    )
    assert client.get("/api/trade_journal/sync").status_code == 405
    assert calls == []
    response = client.post("/api/trade_journal/sync")
    assert response.status_code == 200
    assert response.json()["api_inserted"] == 0
    assert calls == [1]


def test_cors_disabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_CORS_ORIGINS", raising=False)
    client = TestClient(_make_app(tmp_path, monkeypatch))
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options(
        "/api/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_cors_enabled_for_listed_origins_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_CORS_ORIGINS", "https://portal.example, https://ops.example")
    client = TestClient(_make_app(tmp_path, monkeypatch))
    allowed = client.options(
        "/api/health",
        headers={
            "Origin": "https://portal.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Dashboard-Token",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://portal.example"
    assert "x-dashboard-token" in allowed.headers.get("access-control-allow-headers", "").lower()
    denied = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_parse_cors_origins() -> None:
    assert parse_cors_origins(None) == []
    assert parse_cors_origins(" , ") == []
    assert parse_cors_origins("https://a.example,https://b.example ,") == ["https://a.example", "https://b.example"]


# --------------------------------------------------------------------------- FIX 3: health path leak


def _walk_strings(value):  # noqa: ANN001
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_strings(v)
    elif isinstance(value, str):
        yield value


def test_health_payload_has_no_absolute_paths(tmp_path, monkeypatch) -> None:
    client = TestClient(_make_app(tmp_path, monkeypatch))
    body = client.get("/api/health").json()
    for text in _walk_strings(body):
        assert not text.startswith("/"), text
        assert str(tmp_path) not in text, text
        assert "/Users" not in text, text
    assert body["state_file"] == "bot.json"
    assert body["state_file_present"] is False
    assert body["metrics_db"].endswith(".db")
    assert isinstance(body["metrics_db_present"], bool)
    assert body["ledger_dir"] and "/" not in body["ledger_dir"]
    assert body["accounts"][0]["state_file"] == "bot.json"
    assert body["accounts"][0]["state_file_present"] is False


def _assert_no_filesystem_paths(body, *, tmp_path: Path) -> None:  # noqa: ANN001
    for text in _walk_strings(body):
        assert str(tmp_path) not in text, text
        assert "/Users" not in text, text
        assert "/home" not in text, text
        assert not text.startswith("/private/"), text


def test_status_and_groups_payloads_have_no_absolute_paths(tmp_path, monkeypatch) -> None:
    from conftest import FakeClient

    state_path = tmp_path / "bot.json"
    StrategyStateStore(state_path).save(StrategyState(groups=[_closed_group("g1")], next_group_id=2))
    # Real aggregation path (not a stubbed payload) against an in-memory exchange.
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _cfg: FakeClient())
    client = TestClient(_make_app(tmp_path, monkeypatch, with_creds=True))

    status = client.get("/api/status")
    assert status.status_code == 200
    status_body = status.json()
    _assert_no_filesystem_paths(status_body, tmp_path=tmp_path)
    assert [a["state_file"] for a in status_body["dashboard_accounts"]] == ["bot.json"]

    groups = client.get("/api/groups")
    assert groups.status_code == 200
    groups_body = groups.json()
    _assert_no_filesystem_paths(groups_body, tmp_path=tmp_path)
    assert [a["state_file"] for a in groups_body["accounts"]] == ["bot.json"]
    assert len(groups_body["closed"]) == 1


# --------------------------------------------------------------------------- FIX 4: static allowlist


def test_static_allowlist_blocks_repo_internals(tmp_path, monkeypatch) -> None:
    client = TestClient(_make_app(tmp_path, monkeypatch))
    for path in (
        "/package-lock.json",
        "/package.json",
        "/build.mjs",
        "/src/main.js",
        "/src/shared/context.js",
        "/node_modules/esbuild/package.json",
        "/e2e/dashboard.spec.js",
        "/README.md",
        "/../pyproject.toml",
    ):
        assert client.get(path).status_code == 404, path
    for path in ("/app.js", "/app-investor.js", "/styles.css", "/tokens.css", "/favicon.svg", "/vendor/luxon.min.js"):
        assert client.get(path).status_code == 200, path
    assert client.get("/").status_code == 200
    assert "<html" in client.get("/").text.lower()
    assert "immutable" in client.get("/app.js?v=abc").headers["cache-control"]
    assert "no-cache" in client.get("/app.js").headers["cache-control"]


# --------------------------------------------------------------------------- FIX 5: admin console


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
            ]
        ),
        encoding="utf-8",
    )


def test_admin_rejects_non_loopback_client_address(tmp_path, monkeypatch) -> None:
    _write_registry(tmp_path)
    monkeypatch.delenv("ADMIN_CONSOLE_TOKEN", raising=False)
    app = create_admin_app(repo_root=tmp_path)
    remote = TestClient(app, client=("10.0.0.5", 4321))
    # Host header says loopback, but the peer address does not.
    response = remote.get("/api/admin/health", headers={"host": "127.0.0.1:8750"})
    assert response.status_code == 403
    assert "client address" in response.json()["detail"]
    local = TestClient(app, client=("127.0.0.1", 4321))
    assert local.get("/api/admin/health").status_code == 200

    public_app = create_admin_app(repo_root=tmp_path, allow_public=True)
    public = TestClient(public_app, client=("10.0.0.5", 4321))
    ok = public.get("/api/admin/health", headers={"host": "admin.example.com"})
    assert ok.status_code == 200
    assert ok.json()["loopback_only"] is False


def test_admin_token_gate(tmp_path, monkeypatch) -> None:
    _write_registry(tmp_path)
    monkeypatch.setenv("ADMIN_CONSOLE_TOKEN", TOKEN)
    monkeypatch.delenv("ADMIN_CONSOLE_TOKEN_EMBED", raising=False)
    client = TestClient(create_admin_app(repo_root=tmp_path))
    assert client.get("/api/admin/health").status_code == 401
    assert client.get("/api/admin/investors?probe=false").status_code == 401
    assert client.post("/api/admin/investors/alice/panic-close", json={}).status_code == 401
    ok = client.get("/api/admin/health", headers={"X-Admin-Token": TOKEN})
    assert ok.status_code == 200
    assert ok.json()["token_required"] is True
    assert client.get("/api/admin/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    # Page shell stays reachable and does not leak the token unless embedding is opted in.
    page = client.get("/admin.html")
    assert page.status_code == 200
    assert TOKEN not in page.text
    assert client.get("/src/admin.js").status_code == 200


def test_admin_token_embed_opt_in(tmp_path, monkeypatch) -> None:
    _write_registry(tmp_path)
    monkeypatch.setenv("ADMIN_CONSOLE_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_CONSOLE_TOKEN_EMBED", "true")
    client = TestClient(create_admin_app(repo_root=tmp_path))
    assert f'<meta name="admin-console-token" content="{TOKEN}" />' in client.get("/admin.html").text


def test_admin_trading_actions_reject_get(tmp_path, monkeypatch) -> None:
    _write_registry(tmp_path)
    monkeypatch.delenv("ADMIN_CONSOLE_TOKEN", raising=False)
    client = TestClient(create_admin_app(repo_root=tmp_path))
    for path in (
        "/api/admin/investors/alice/panic-close",
        "/api/admin/investors/alice/close-position",
        "/api/admin/investors/alice/spot-restore",
        "/api/admin/investors/alice/csp-abort-restore",
        "/api/admin/frontend/alice/restart",
        "/api/admin/frontend/alice/stop",
        "/api/admin/frontend/alice/start",
    ):
        assert client.get(path).status_code == 405, path


def test_admin_static_allowlist(tmp_path, monkeypatch) -> None:
    _write_registry(tmp_path)
    monkeypatch.delenv("ADMIN_CONSOLE_TOKEN", raising=False)
    client = TestClient(create_admin_app(repo_root=tmp_path))
    for path in ("/src/admin.js", "/styles.css", "/tokens.css", "/tailwind.css", "/favicon.svg"):
        assert client.get(path).status_code == 200, path
    for path in (
        "/package-lock.json",
        "/src/main.js",
        "/src/shared/context.js",
        "/node_modules/esbuild/package.json",
        "/app.js",
        "/index.html.bak",
    ):
        assert client.get(path).status_code == 404, path


# --------------------------------------------------------------------------- FIX 6: journal bulk read


def _closed_group(group_id: str) -> TradeGroup:
    return TradeGroup(
        group_id=group_id,
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1_699_000_000_000,
        expiration_timestamp_ms=future_expiry(30),
        short_instrument_name="ETH-29MAR24-3000-C",
        short_strike=Decimal("3000"),
        entry_credit=Decimal("38"),
        original_entry_credit=Decimal("38"),
        max_loss=Decimal("250"),
        regime_at_entry="normal",
        entry_fee=Decimal("0.5"),
        status="closed",
        strategy="naked_short",
        closed_timestamp_ms=1_700_000_000_000,
        realized_pnl=Decimal("23"),
        realized_close_debit=Decimal("14"),
        realized_close_fee=Decimal("0.5"),
    )


def _seed_journal(state_path: Path, group_ids: list[str], rows_per_group: int) -> None:
    store = TradeJournalStore(journal_db_path_for_state(state_path))
    scope = scope_key_for_state(state_path)
    for gid in group_ids:
        for i in range(rows_per_group):
            store.record_fill(
                scope_key=scope,
                event_type="close",
                source_action="test",
                instrument_name="ETH-29MAR24-3000-C",
                direction="buy",
                amount=Decimal("1"),
                price=Decimal(str(10 + i)),
                group_id=gid,
                trade_id=f"{gid}-{i}",
                ts_ms=1_700_000_000_000 + i,
            )


def test_journal_executions_by_group_buckets_and_caps(tmp_path) -> None:
    state_path = tmp_path / "bot.json"
    StrategyStateStore(state_path).save(StrategyState())
    _seed_journal(state_path, ["g1", "g2"], rows_per_group=60)
    out = groups_service._journal_executions_by_group(state_path, ["g1", "g2", "missing"])
    assert set(out) == {"g1", "g2", "missing"}
    assert out["missing"] == []
    assert len(out["g1"]) == 50 and len(out["g2"]) == 50
    # Newest first, same shape as TradeJournalStore.list_executions.
    assert out["g1"][0]["ts_ms"] == 1_700_000_000_000 + 59
    assert out["g1"][0]["trade_id"] == "g1-59"
    assert out["g1"][-1]["ts_ms"] == 1_700_000_000_000 + 10
    reference = TradeJournalStore(journal_db_path_for_state(state_path)).list_executions(
        scope_key_for_state(state_path), group_id="g2", limit=50
    )
    assert out["g2"] == reference
    # No journal file → empty buckets, no error.
    assert groups_service._journal_executions_by_group(tmp_path / "other.json", ["x"]) == {"x": []}


def test_closed_groups_payload_opens_journal_once(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "bot.json"
    group_ids = [f"g{i}" for i in range(8)]
    StrategyStateStore(state_path).save(StrategyState(groups=[_closed_group(g) for g in group_ids], next_group_id=9))
    _seed_journal(state_path, group_ids, rows_per_group=3)

    from deribit_engine import sqlite_store_base

    store_inits: list[Path] = []
    connects: list[str] = []
    real_connect = sqlite_store_base.sqlite3.connect

    class _CountingStore(TradeJournalStore):
        def __init__(self, path, *a, **k):
            store_inits.append(Path(path))
            super().__init__(path, *a, **k)

    def _counting_connect(path, *a, **k):
        connects.append(str(path))
        return real_connect(path, *a, **k)

    monkeypatch.setattr(groups_service, "TradeJournalStore", _CountingStore)
    monkeypatch.setattr(sqlite_store_base.sqlite3, "connect", _counting_connect)
    payload = groups_service._load_closed_groups_payload(state_path, spot_index={"ETH": Decimal("2400")})
    assert len(payload["closed"]) == len(group_ids)
    # One store for all N groups: one schema-init connection + one bulk SELECT connection.
    assert len(store_inits) == 1
    assert len(connects) == 2, connects


# --------------------------------------------------------------------------- FIX 7: ledger tail + fsync


def _write_rows(path: Path, rows: list[dict], *, trailer: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row) + "\n")
        fp.write(trailer)


def test_latest_ledger_row_reads_tail_of_newest_file(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ledger"
    _write_rows(root / "equity_20240101.jsonl", [{"ts_ms": 1, "total_equity_usdc": "1"}])
    _write_rows(
        root / "equity_20240102.jsonl",
        [{"ts_ms": 2, "total_equity_usdc": "2"}, {"ts_ms": 3, "total_equity_usdc": "3"}],
        trailer="\n\n{not json\n",
    )

    def _never(*_a, **_k):
        raise AssertionError("_latest_ledger_row must not read the whole ledger")

    monkeypatch.setattr(helpers, "_read_ledger", _never)
    assert helpers._latest_ledger_row(root) == {"ts_ms": 3, "total_equity_usdc": "3"}


def test_latest_ledger_row_falls_back_to_previous_file(tmp_path) -> None:
    root = tmp_path / "ledger"
    _write_rows(root / "equity_20240101.jsonl", [{"ts_ms": 1}])
    (root / "equity_20240102.jsonl").write_text("\n\n", encoding="utf-8")
    (root / "equity_20240103.jsonl").write_text("", encoding="utf-8")
    assert helpers._latest_ledger_row(root) == {"ts_ms": 1}
    assert helpers._latest_ledger_row(tmp_path / "nope") is None


def test_latest_ledger_row_handles_rows_larger_than_tail_chunk(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ledger"
    big = {"ts_ms": 9, "blob": "x" * 5000}
    _write_rows(root / "equity_20240101.jsonl", [{"ts_ms": 1, "blob": "y" * 5000}, big])
    monkeypatch.setattr(helpers, "_LEDGER_TAIL_CHUNK_BYTES", 256)
    assert helpers._latest_ledger_row(root) == big


def test_append_ledger_fsyncs(tmp_path, monkeypatch) -> None:
    synced: list[int] = []
    real_fsync = helpers.os.fsync

    def _fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(helpers.os, "fsync", _fsync)
    root = tmp_path / "ledger"
    helpers._append_ledger(root, {"ts_ms": 42, "total_equity_usdc": "1"})
    assert len(synced) == 1
    assert helpers._latest_ledger_row(root) == {"ts_ms": 42, "total_equity_usdc": "1"}


# --------------------------------------------------------------------------- FIX 8: bundle ETag


def test_dashboard_bundle_etag_304(tmp_path, monkeypatch) -> None:
    fake_status = {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {}}
    fake_summary = {"summary": {"realized_pnl_usdc": "50"}, "recent_closed_trades": []}
    monkeypatch.setattr(frontend_server, "_aggregate_status", lambda *_a, **_k: fake_status)
    monkeypatch.setattr(frontend_server, "_aggregate_groups", lambda *_a, **_k: fake_groups)
    monkeypatch.setattr(frontend_server, "_aggregate_realized_summary", lambda *_a, **_k: fake_summary)
    client = TestClient(_make_app(tmp_path, monkeypatch, with_creds=True))

    first = client.get("/api/dashboard_bundle")
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('W/"')
    assert first.json()["status"]["portfolio"]["total_equity_usdc"] == "1000"

    second = client.get("/api/dashboard_bundle", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.content == b""

    third = client.get("/api/dashboard_bundle", headers={"If-None-Match": 'W/"stale"'})
    assert third.status_code == 200
    # The cached payload must not be mutated by finalize (route no longer deep-copies).
    assert fake_status == {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}


# --------------------------------------------------------------------------- FIX 9: bounded background work


def test_single_flight_runner_dedupes_concurrent_submits() -> None:
    executor = make_background_executor()
    runner = SingleFlightRunner(executor)
    calls: list[int] = []
    release = threading.Event()

    def _slow() -> None:
        release.wait(timeout=5)
        calls.append(1)

    accepted = [runner.submit("k", _slow) for _ in range(20)]
    assert accepted.count(True) == 1
    assert runner.inflight_count == 1
    release.set()
    deadline = time.monotonic() + 5
    while runner.inflight_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [1]
    assert runner.submit("k", lambda: calls.append(2)) is True
    executor.shutdown(wait=True)
    assert calls == [1, 2]
    # Closed pool: submit reports False instead of raising.
    assert runner.submit("k2", lambda: None) is False


def test_ttl_cache_swr_refresh_single_flight_on_executor() -> None:
    executor = make_background_executor()
    cache = _TtlCache(0.001, stale_while_revalidate=True, executor=executor)
    cache.seed("k", "stale")
    time.sleep(0.01)
    factory_calls: list[int] = []
    gate = threading.Event()

    def _factory() -> str:
        gate.wait(timeout=5)
        factory_calls.append(1)
        return "fresh"

    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_set("k", _factory))) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert results == ["stale"] * 20
    gate.set()
    executor.shutdown(wait=True)
    assert factory_calls == [1]
    assert cache.get_stale("k") == "fresh"
    names = {t.name for t in threading.enumerate()}
    assert not any(name == "ttl-cache-swr" for name in names)


def test_transfers_live_refresh_uses_single_flight(tmp_path, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    compute_calls: list[int] = []

    def _fake_aggregate(*_a, **_k):
        compute_calls.append(1)
        started.set()
        release.wait(timeout=5)
        return {"accounts": [], "days_requested": 90}

    monkeypatch.setattr(
        "deribit_engine.frontend_server.transfers_service.aggregate_transfers_for_accounts",
        _fake_aggregate,
    )
    monkeypatch.setattr(
        "deribit_engine.frontend_server.transfers_service.build_transfers_payload_from_store",
        lambda *_a, **_k: {"accounts": [], "days_requested": 90, "source": "store"},
    )
    client = TestClient(_make_app(tmp_path, monkeypatch, with_creds=True))
    for _ in range(20):
        response = client.get("/api/transfers?days=90&limit=50")
        assert response.status_code == 200
        assert response.headers.get("x-transfer-store") == "true"
    assert started.wait(timeout=5)
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(compute_calls) < 1:
        time.sleep(0.01)
    time.sleep(0.05)
    assert compute_calls == [1]
    bg_names = [t.name for t in threading.enumerate() if t.name.startswith("transfers-bg-refresh")]
    assert bg_names == []


# --------------------------------------------------------------------------- FIX 10: identity hash


def test_live_api_identity_hides_secret(tmp_path) -> None:
    cfg = make_config(tmp_path, client_id="Key-ID", client_secret="very-secret-value")
    identity = helpers._live_api_identity_config(cfg, "label")
    assert "very-secret-value" not in identity
    assert "Key-ID" not in identity and "key-id" not in identity
    assert len(identity) == 16
    same = make_config(tmp_path, client_id="key-id ", client_secret="very-secret-value")
    assert helpers._live_api_identity_config(same, "other") == identity
    other = make_config(tmp_path, client_id="key-id", client_secret="different")
    assert helpers._live_api_identity_config(other, "label") != identity
    nocreds = make_config(tmp_path, client_id="", client_secret="")
    assert helpers._live_api_identity_config(nocreds, "acct") == "noid:acct"


# --------------------------------------------------------------------------- FIX 11: scheduler stop()


def test_scheduler_stop_reports_stuck_thread(tmp_path, caplog) -> None:
    cfg = make_config(tmp_path)
    scheduler = EquitySnapshotScheduler(
        account_name="a",
        bot_factory=lambda: None,  # type: ignore[return-value]
        interval_sec=30,
        ledger_root=tmp_path / "ledger",
        config=cfg,
    )
    release = threading.Event()
    stuck = threading.Thread(target=lambda: release.wait(timeout=10), daemon=True)
    stuck.start()
    scheduler._thread = stuck
    scheduler.state.running = True
    with caplog.at_level(logging.WARNING, logger="deribit_engine.frontend_server.types"):
        assert scheduler.stop(timeout=0.05) is False
    assert scheduler.state.running is True
    assert scheduler.state.stop_requested is True
    assert any("still running after 0.05s" in rec.getMessage() for rec in caplog.records)
    release.set()
    stuck.join(timeout=2)
    assert scheduler.stop(timeout=0.5) is True
    assert scheduler.state.running is False


def test_scheduler_stop_without_thread_is_clean(tmp_path) -> None:
    cfg = make_config(tmp_path)
    scheduler = EquitySnapshotScheduler(
        account_name="a",
        bot_factory=lambda: None,  # type: ignore[return-value]
        interval_sec=30,
        ledger_root=tmp_path / "ledger",
        config=cfg,
    )
    assert scheduler.stop() is True
    assert scheduler.state.running is False
    assert scheduler.state.stop_requested is True
