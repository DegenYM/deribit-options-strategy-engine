from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import deribit_demo.frontend_server as frontend_server
from deribit_demo.current_stress import CurrentStressResult
from deribit_demo.frontend_server import DashboardAccount
from deribit_demo.models import StrategyState, TradeGroup
from deribit_demo.state import StrategyStateStore, performance_exclusions_path
from deribit_demo.stress import black_swan_strategy_analysis
from conftest import future_expiry, make_config


def _stress_result(strategy: str, book: str, loss: Decimal) -> CurrentStressResult:
    return CurrentStressResult(
        generated_at="now",
        option_strategy=strategy,
        strategy_analysis=black_swan_strategy_analysis(strategy),
        index_by_ccy={"BTC": Decimal("100000"), "ETH": Decimal("5000"), "USDC": Decimal("1")},
        equity_usdc_by_book={book: Decimal("1000")},
        positions=[{"instrument_name": f"{strategy}-leg"}],
        scenarios=[
            {
                "shock": "-0.10",
                "slippage": "0.05",
                "loss_usdc_total": str(loss),
                "loss_by_book_usdc": {book: str(loss)},
                "components_total_usdc": {"base_move_usdc": str(loss)},
                "worst_legs": [{"instrument_name": f"{strategy}-leg", "loss_usdc": str(loss)}],
            }
        ],
        notes=[],
    )


def _rolling_apr_brute(
    closed: list[dict],
    *,
    window_days: int,
    effective_capital_usdc: Decimal,
    max_chart_days: int = 730,
) -> list[dict]:
    """Reference implementation (slow) for regression tests."""
    if effective_capital_usdc <= 0:
        return []
    realized = sorted(
        [g for g in closed if g.get("closed_timestamp_ms") is not None and g.get("realized_pnl") is not None],
        key=lambda g: int(g["closed_timestamp_ms"]),
    )
    if not realized:
        return []
    first_day = datetime.fromtimestamp(int(realized[0]["closed_timestamp_ms"]) / 1000, tz=UTC).date()
    last_day = datetime.fromtimestamp(int(realized[-1]["closed_timestamp_ms"]) / 1000, tz=UTC).date()
    today = datetime.now(tz=UTC).date()
    if today > last_day:
        last_day = today
    chart_start = first_day
    if max_chart_days > 0 and (last_day - first_day).days + 1 > max_chart_days:
        chart_start = last_day - timedelta(days=max_chart_days - 1)
    pnl_by_day: dict[str, Decimal] = {}
    for g in realized:
        day = frontend_server._bucket_day_utc(int(g["closed_timestamp_ms"]))
        pnl_by_day[day] = pnl_by_day.get(day, Decimal("0")) + Decimal(str(g["realized_pnl"]))
    rows: list[dict] = []
    cursor = chart_start
    while cursor <= last_day:
        window_start = cursor - timedelta(days=window_days - 1)
        window_pnl = Decimal("0")
        d = window_start
        while d <= cursor:
            window_pnl += pnl_by_day.get(d.strftime("%Y-%m-%d"), Decimal("0"))
            d += timedelta(days=1)
        annualized = (window_pnl * Decimal("365") / Decimal(str(window_days))) / effective_capital_usdc
        rows.append(
            {
                "date": cursor.strftime("%Y-%m-%d"),
                "apr": str(annualized),
                "window_pnl_usdc": str(window_pnl),
            }
        )
        cursor += timedelta(days=1)
    return rows


def test_rolling_apr_series_matches_brute_force():
    capital = Decimal("10000")
    base_ms = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    closed = []
    for i in range(120):
        closed.append(
            {
                "closed_timestamp_ms": base_ms + i * 86400 * 1000,
                "realized_pnl": str(Decimal("10") if i % 3 == 0 else Decimal("-2")),
            }
        )
    for window_days in (7, 14, 30, 60):
        fast = frontend_server._rolling_apr_series(
            closed,
            window_days=window_days,
            effective_capital_usdc=capital,
            max_chart_days=90,
        )
        slow = _rolling_apr_brute(
            closed,
            window_days=window_days,
            effective_capital_usdc=capital,
            max_chart_days=90,
        )
        assert fast == slow


def test_rolling_apr_series_caps_chart_points():
    capital = Decimal("5000")
    base_ms = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
    closed = [
        {
            "closed_timestamp_ms": base_ms + i * 86400 * 1000,
            "realized_pnl": "1",
        }
        for i in range(2000)
    ]
    rows = frontend_server._rolling_apr_series(
        closed,
        window_days=30,
        effective_capital_usdc=capital,
        max_chart_days=100,
    )
    assert len(rows) <= 100


def test_closed_groups_payload_excludes_performance_groups(tmp_path):
    state_path = tmp_path / "covered_call.json"
    state = StrategyState()
    for group_id in ("0001", "0002"):
        group = TradeGroup(
            group_id=group_id,
            currency="ETH",
            collateral_currency="ETH",
            quantity=Decimal("1"),
            entry_timestamp_ms=1,
            expiration_timestamp_ms=future_expiry(7),
            short_instrument_name=f"ETH-14APR30-26{group_id[-1]}0-C",
            short_strike=Decimal("2600"),
            entry_credit=Decimal("30"),
            original_entry_credit=Decimal("30"),
            max_loss=Decimal("250"),
            regime_at_entry="normal",
            status="closed",
            closed_timestamp_ms=2,
            realized_pnl=Decimal("1"),
            option_type="call",
            strategy="covered_call",
        )
        state.groups.append(group)
    StrategyStateStore(state_path).save(state)
    performance_exclusions_path(state_path).write_text(
        '{"excluded_group_ids": ["0001"]}',
        encoding="utf-8",
    )

    payload = frontend_server._closed_groups_payload(state_path)

    assert [group["group_id"] for group in payload["closed"]] == ["0002"]
    assert payload["performance_excluded_closed_group_count"] == 1


def test_aggregate_stress_returns_per_strategy_sections(tmp_path, monkeypatch):
    put_cfg = make_config(tmp_path, option_strategy="naked_short", client_id="id-put")
    call_cfg = make_config(tmp_path, option_strategy="covered_call", client_id="id-call")
    put_env = tmp_path / "put.env"
    call_env = tmp_path / "call.env"
    configs = {put_env: put_cfg, call_env: call_cfg}
    accounts = [
        DashboardAccount("put", put_env, put_cfg, put_cfg.state_file, tmp_path / "ledger-put"),
        DashboardAccount("call", call_env, call_cfg, call_cfg.state_file, tmp_path / "ledger-call"),
    ]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert shocks == [Decimal("-0.10")]
        if config.option_strategy == "covered_call":
            return _stress_result("covered_call", "BTC", Decimal("-50"))
        return _stress_result("naked_short", "USDC", Decimal("-100"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: configs[env_file])
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    assert payload["option_strategy"] == "multi_account"
    assert payload["scenarios"][0]["loss_usdc_total"] == Decimal("-150")
    assert payload["scenarios"][0]["loss_usdc_pct_of_total_equity"] == Decimal("-0.075")

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"naked_short", "covered_call"}
    assert per_strategy["naked_short"]["scenarios"][0]["loss_by_book_usdc"] == {"USDC": Decimal("-100")}
    assert per_strategy["covered_call"]["strategy_analysis"]["label"] == "covered_call"
    assert per_strategy["covered_call"]["accounts"][0]["name"] == "call"


def test_aggregate_stress_dedupes_shared_api_credentials(tmp_path, monkeypatch):
    """Same DERIBIT_CLIENT_ID/SECRET in two rows is one exchange account — aggregate risk once."""
    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="dup")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="dup")
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    configs = {env_a: cfg_a, env_b: cfg_b}
    accounts = [
        DashboardAccount("put", env_a, cfg_a, cfg_a.state_file, tmp_path / "ledger-a"),
        DashboardAccount("call", env_b, cfg_b, cfg_b.state_file, tmp_path / "ledger-b"),
    ]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert shocks == [Decimal("-0.10")]
        if config.option_strategy == "covered_call":
            return _stress_result("covered_call", "BTC", Decimal("-50"))
        return _stress_result("naked_short", "USDC", Decimal("-100"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: configs[env_file])
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    assert payload["option_strategy"] == "multi_account"
    assert payload["scenarios"][0]["loss_usdc_total"] == Decimal("-100")
    assert payload["scenarios"][0]["loss_usdc_pct_of_total_equity"] == Decimal("-0.1")

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"naked_short", "covered_call"}
    assert len(per_strategy["naked_short"]["accounts"]) == 1
    assert len(per_strategy["covered_call"]["accounts"]) == 1


def test_aggregate_stress_normalizes_put_spread_alias(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, option_strategy="bull_put_spread")
    env = tmp_path / "spread.env"
    accounts = [DashboardAccount("spread", env, cfg, cfg.state_file, tmp_path / "ledger-spread")]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert config.option_strategy == "bull_put_spread"
        return _stress_result("put_spread", "USDC", Decimal("-25"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"bull_put_spread"}
    assert per_strategy["bull_put_spread"]["strategy_analysis"]["label"] == "bull_put_spread"


def test_aggregate_status_prefetch_once_per_api_identity(tmp_path, monkeypatch):
    """Three strategy rows on one Deribit login should not triple Deribit fan-out."""
    from deribit_demo.engine import ExchangePrefetch

    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="dup", client_secret="same")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="dup", client_secret="same")
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    accounts = [
        DashboardAccount("a", env_a, cfg_a, cfg_a.state_file, tmp_path / "ledger-a"),
        DashboardAccount("b", env_b, cfg_b, cfg_b.state_file, tmp_path / "ledger-b"),
    ]
    prefetch = ExchangePrefetch(
        summaries={},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": [], "ETH": []},
    )
    prefetch_calls = 0

    class FakeBot:
        def fetch_exchange_prefetch(self) -> ExchangePrefetch:
            nonlocal prefetch_calls
            prefetch_calls += 1
            return prefetch

        def status_with_exchange_prefetch(self, _prefetch: ExchangePrefetch) -> dict:
            return {
                "env": "testnet",
                "portfolio": {"total_equity_usdc": Decimal("1000"), "equity_by_book": {"USDC": Decimal("1000")}},
                "underlying_index_usd": {"BTC": "1", "ETH": "1"},
                "accounts": {"USDC": {"equity": "1000"}},
                "trade_groups": [],
                "open_orders": [],
                "positions": [],
                "trade_group_count": 0,
            }

    monkeypatch.setattr(frontend_server, "_bot_for_account", lambda account, require_private=True: FakeBot())
    cache = frontend_server._TtlCache(15.0)
    payload = frontend_server._aggregate_status(accounts, exchange_prefetch_cache=cache)

    assert prefetch_calls == 1
    assert payload["trade_group_count"] == 0


def test_aggregate_portfolios_sums_spot_excluded_day_pnl(tmp_path):
    cfg = make_config(tmp_path)
    accounts = [
        DashboardAccount("a", tmp_path / "a.env", cfg, cfg.state_file, tmp_path / "ledger-a"),
        DashboardAccount("b", tmp_path / "b.env", cfg, cfg.state_file, tmp_path / "ledger-b"),
    ]
    statuses = [
        {
            "portfolio": {
                "total_equity_usdc": Decimal("1000"),
                "day_start_equity_usdc": Decimal("950"),
                "day_net_flow_usdc": Decimal("10"),
                "day_pnl_usdc_ex_flow": Decimal("40"),
                "day_pnl_usdc_ex_flow_ex_spot": Decimal("5"),
                "equity_by_book": {"ETH": Decimal("1000")},
                "day_start_equity_by_book": {"ETH": Decimal("950")},
                "day_net_flow_usdc_by_book": {"ETH": Decimal("10")},
                "day_pnl_usdc_ex_flow_by_book": {"ETH": Decimal("40")},
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {"ETH": Decimal("5")},
            }
        },
        {
            "portfolio": {
                "total_equity_usdc": Decimal("500"),
                "day_start_equity_usdc": Decimal("500"),
                "day_net_flow_usdc": Decimal("0"),
                "day_pnl_usdc_ex_flow": Decimal("0"),
                "day_pnl_usdc_ex_flow_ex_spot": Decimal("12"),
                "equity_by_book": {"USDC": Decimal("500")},
                "day_start_equity_by_book": {"USDC": Decimal("500")},
                "day_net_flow_usdc_by_book": {"USDC": Decimal("0")},
                "day_pnl_usdc_ex_flow_by_book": {"USDC": Decimal("0")},
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {"USDC": Decimal("12")},
            }
        },
    ]

    portfolio = frontend_server._aggregate_portfolios(accounts, statuses)

    assert portfolio["day_pnl_usdc_ex_flow"] == Decimal("40")
    assert portfolio["day_pnl_usdc_ex_flow_ex_spot"] == Decimal("17")
    assert portfolio["day_pnl_usdc_ex_flow_ex_spot_by_book"] == {
        "ETH": Decimal("5"),
        "USDC": Decimal("12"),
    }


def test_aggregate_portfolios_dedupes_shared_api_credentials(tmp_path):
    shared = make_config(tmp_path, client_id="shared-key", client_secret="s")
    other = make_config(tmp_path, client_id="other-key", client_secret="s", state_file=tmp_path / "other.json")
    accounts = [
        DashboardAccount("s1", tmp_path / "x.env", shared, shared.state_file, tmp_path / "ledger-x"),
        DashboardAccount("s2", tmp_path / "y.env", shared, shared.state_file, tmp_path / "ledger-y"),
        DashboardAccount("o", tmp_path / "z.env", other, other.state_file, tmp_path / "ledger-z"),
    ]
    statuses: list[dict] = [
        {"portfolio": {"total_equity_usdc": Decimal("900"), "day_start_equity_usdc": Decimal("900")}},
        {"portfolio": {"total_equity_usdc": Decimal("900"), "day_start_equity_usdc": Decimal("900")}},
        {"portfolio": {"total_equity_usdc": Decimal("100"), "day_start_equity_usdc": Decimal("100")}},
    ]
    equity_statuses = frontend_server._dedupe_statuses_for_equity_aggregate(accounts, statuses)
    assert len(equity_statuses) == 2
    portfolio = frontend_server._aggregate_portfolios(accounts, statuses, equity_statuses=equity_statuses)
    assert portfolio["total_equity_usdc"] == Decimal("1000")


def test_dedupe_merges_equity_by_book_for_shared_credentials(tmp_path):
    """Same API key + different TRADED_COLLATERALS must union per-book rows on one snapshot."""
    shared = make_config(tmp_path, client_id="shared-key", client_secret="s")
    accounts = [
        DashboardAccount("inverse_first", tmp_path / "a.env", shared, shared.state_file, tmp_path / "ledger-a"),
        DashboardAccount("linear_second", tmp_path / "b.env", shared, shared.state_file, tmp_path / "ledger-b"),
    ]
    statuses: list[dict] = [
        {
            "portfolio": {
                "total_equity_usdc": Decimal("7000"),
                "equity_by_book": {"BTC": Decimal("3000"), "ETH": Decimal("2000")},
                "day_start_equity_by_book": {"BTC": Decimal("3000"), "ETH": Decimal("2000")},
            }
        },
        {
            "portfolio": {
                "total_equity_usdc": Decimal("7000"),
                "equity_by_book": {"USDC": Decimal("7000")},
                "day_start_equity_by_book": {"USDC": Decimal("6900")},
            }
        },
    ]
    merged = frontend_server._dedupe_statuses_for_equity_aggregate(accounts, statuses)
    assert len(merged) == 1
    eb = merged[0]["portfolio"]["equity_by_book"]
    assert eb["BTC"] == Decimal("3000")
    assert eb["ETH"] == Decimal("2000")
    assert eb["USDC"] == Decimal("7000")
    portfolio = frontend_server._aggregate_portfolios(accounts, statuses, equity_statuses=merged)
    assert portfolio["total_equity_usdc"] == Decimal("7000")
    assert portfolio["equity_by_book"]["USDC"] == Decimal("7000")


def test_aggregate_realized_summary_is_json_serializable(tmp_path, monkeypatch) -> None:
    import json

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=test\n", encoding="utf-8")
    state_path = tmp_path / "bot.json"
    cfg = make_config(state_file=state_path)
    account = DashboardAccount(
        name="test",
        env_file=env_file,
        config=cfg,
        state_path=state_path,
        ledger_root=tmp_path / "ledger",
    )
    payload = frontend_server._aggregate_realized_summary([account], days=30)
    json.dumps(frontend_server._decimalize(payload))


def test_trade_journal_sync_scheduler_run_once(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=test\n", encoding="utf-8")
    cfg = make_config(state_file=tmp_path / "bot.json")
    account = DashboardAccount(
        name="test",
        env_file=env_file,
        config=cfg,
        state_path=Path(cfg.state_file),
        ledger_root=tmp_path / "ledger",
    )
    calls: list[Path] = []

    def fake_sync(env_path: Path, **kwargs: object) -> dict[str, object]:
        calls.append(env_path)
        return {"api_inserted": 2}

    monkeypatch.setattr(frontend_server, "_has_private_creds", lambda _cfg: True)
    monkeypatch.setattr(frontend_server, "sync_incremental_journal", fake_sync)
    scheduler = frontend_server.TradeJournalSyncScheduler(accounts=[account], interval_sec=120)
    payload = scheduler.run_once()
    assert calls == [env_file]
    assert payload["api_inserted"] == 2
    assert scheduler.state.last_inserted == 2
    assert scheduler.state.last_success_ms is not None
    assert scheduler.state.last_error is None


def test_backfill_row_collateral_native_uses_spot_for_legacy_closed_row() -> None:
    row = {
        "status": "closed",
        "group_id": "0040",
        "short_instrument_name": "ETH-29MAY26-2300-C",
        "collateral_currency": "ETH",
        "currency": "ETH",
        "quantity": "1",
        "entry_credit": "38.044948",
        "entry_fee": "0.663477",
        "short_entry_average_price": "0.0175",
        "short_close_average_price": "0.0065",
        "entry_index_usd": "2211.88",
        "close_index_usd": "2103.84",
        "realized_close_debit": "14.306112",
        "realized_close_fee": "0.631152",
        "realized_pnl": "23.738836",
    }
    frontend_server._backfill_row_collateral_native(row, {"ETH": Decimal("2400")})
    native = Decimal(str(row["realized_pnl_collateral_native"]))
    assert native > Decimal("0")
    assert native < Decimal("0.0105")
    assert abs(native * Decimal("2400") - Decimal(str(row["realized_pnl"]))) < Decimal("0.05")
    assert isinstance(row["realized_pnl_collateral_native"], str)


def test_aggregate_groups_payload_json_serializable_after_spot_backfill(tmp_path) -> None:
    import json

    state_path = tmp_path / "bot.json"
    store = StrategyStateStore(state_path)
    group = TradeGroup(
        group_id="g1",
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
    state = StrategyState(groups=[group], next_group_id=2)
    store.save(state)
    payload = frontend_server._closed_groups_payload(state_path)
    frontend_server._apply_spot_native_backfill(payload, {"ETH": Decimal("2400")})
    json.dumps(frontend_server._decimalize(payload))
