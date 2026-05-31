from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from conftest import future_expiry, make_config

import deribit_engine.frontend_server as frontend_server
from deribit_engine.current_stress import CurrentStressResult
from deribit_engine.frontend_server import DashboardAccount
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore, performance_exclusions_path
from deribit_engine.stress import black_swan_strategy_analysis


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
                "equity_usdc": str(effective_capital_usdc),
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


def test_rolling_apr_uses_equity_on_sample_day_not_later_deposit():
    today = datetime.now(tz=UTC).date()
    day = today - timedelta(days=10)
    pnl_by_day = {day: Decimal("100")}
    equity_by_day = {
        day: Decimal("10000"),
        day + timedelta(days=1): Decimal("100000"),
    }
    rows = frontend_server._rolling_apr_from_daily_totals(
        pnl_by_day,
        window_days=30,
        effective_capital_usdc=Decimal("100000"),
        equity_by_day=equity_by_day,
        max_chart_days=90,
    )
    row = next(r for r in rows if r["date"] == day.strftime("%Y-%m-%d"))
    apr = Decimal(row["apr"])
    expected = (Decimal("100") * Decimal("365") / Decimal("30")) / Decimal("10000")
    assert apr == expected
    assert row["equity_usdc"] == "10000"


def test_resolve_apr_effective_capital_prefers_equity(tmp_path):
    cfg = make_config(tmp_path, reference_capital_usdc=Decimal("1000"))
    account = DashboardAccount("a", tmp_path / "a.env", cfg, cfg.state_file, tmp_path / "ledger-a")
    status = {"portfolio": {"total_equity_usdc": "25000"}}
    assert frontend_server._resolve_apr_effective_capital_usdc(
        [account],
        override=None,
        status_payload=status,
    ) == Decimal("25000")
    assert frontend_server._resolve_apr_effective_capital_usdc(
        [account],
        override=Decimal("18000"),
        status_payload=status,
    ) == Decimal("18000")


def test_resolve_apr_effective_capital_falls_back_to_reference(tmp_path):
    cfg = make_config(tmp_path, reference_capital_usdc=Decimal("1500"))
    account = DashboardAccount("a", tmp_path / "a.env", cfg, cfg.state_file, tmp_path / "ledger-a")
    assert frontend_server._resolve_apr_effective_capital_usdc(
        [account],
        override=None,
        status_payload=None,
    ) == Decimal("1500")


def test_group_strike_parsed_from_instrument_name():
    group = {"short_instrument_name": "ETH_USDC-24APR26-1700-P"}
    assert frontend_server._group_strike(group) == Decimal("1700")


def test_ensure_realized_apr_uses_strike_from_instrument_name():
    group = {
        "short_instrument_name": "ETH_USDC-24APR26-1700-P",
        "collateral_currency": "USDC",
        "option_type": "put",
        "strategy": "naked_short",
        "quantity": "1",
        "realized_pnl": "-15",
        "entry_timestamp_ms": 1_000_000,
        "closed_timestamp_ms": 1_000_000 + 5 * 86_400_000,
    }
    frontend_server._ensure_realized_apr_on_equity(
        group,
        index_usd={"USDC": Decimal("1"), "ETH": Decimal("2000")},
        contract_size=Decimal("0.1"),
    )
    apr = Decimal(group["realized_apr_on_equity"])
    expected = (Decimal("-15") / Decimal("1700") / Decimal("5")) * Decimal("365")
    assert abs(apr - expected) < Decimal("0.000001")
    assert apr < 0
    assert Decimal(group["short_strike"]) == Decimal("1700")


def test_ttl_cache_try_get():
    cache = frontend_server._TtlCache(60.0)
    assert cache.try_get("missing") is None
    assert cache.get_or_set("k", lambda: {"x": 1}) == {"x": 1}
    assert cache.try_get("k") == {"x": 1}


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


def test_closed_groups_payload_excludes_phantom_reconcile_close(tmp_path):
    short = "ETH_USDC-29MAY26-2350-C"
    state_path = tmp_path / "naked.json"
    state = StrategyState()
    phantom = TradeGroup(
        group_id="0007",
        currency="ETH",
        collateral_currency="USDC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=1_000,
        expiration_timestamp_ms=future_expiry(7),
        short_instrument_name=short,
        short_strike=Decimal("2350"),
        entry_credit=Decimal("10"),
        original_entry_credit=Decimal("10"),
        max_loss=Decimal("50"),
        regime_at_entry="normal",
        status="closed",
        close_reason="reconciled_external",
        closed_timestamp_ms=4_000,
        realized_pnl=Decimal("-3"),
        strategy="naked_short",
    )
    live = TradeGroup(
        group_id="0008",
        currency="ETH",
        collateral_currency="USDC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=20_000,
        expiration_timestamp_ms=future_expiry(7),
        short_instrument_name=short,
        short_strike=Decimal("2350"),
        entry_credit=Decimal("10"),
        original_entry_credit=Decimal("10"),
        max_loss=Decimal("50"),
        regime_at_entry="normal",
        status="open",
        last_action="adopted_from_exchange",
        strategy="naked_short",
    )
    state.groups.extend([phantom, live])
    StrategyStateStore(state_path).save(state)

    payload = frontend_server._closed_groups_payload(state_path)

    assert payload["open"] and payload["open"][0]["group_id"] == "0008"
    assert payload["closed"] == []


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
    from deribit_engine.engine import ExchangePrefetch

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

        def status_with_exchange_prefetch(self, _prefetch: ExchangePrefetch, **_kwargs) -> dict:
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
    cfg = make_config(tmp_path, state_file=state_path)
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
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json")
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


def _write_ledger_row(root: Path, *, ts_ms: int, equity: str, client_suffix: str = "a") -> None:
    root.mkdir(parents=True, exist_ok=True)
    frontend_server._append_ledger(
        root,
        {
            "ts_ms": ts_ms,
            "account_name": f"acct-{client_suffix}",
            "env": "testnet",
            "option_strategy": "naked_short",
            "total_equity_usdc": equity,
            "day_start_equity_usdc": str(Decimal(equity) - Decimal("10")),
            "day_net_flow_usdc": "0",
            "day_pnl_usdc_ex_flow": "10",
            "day_pnl_usdc_ex_flow_ex_spot": "10",
            "day_drawdown_pct": "0.01",
            "open_max_loss_usdc": "100",
            "equity_by_book": {"USDC": equity},
            "day_start_equity_by_book": {"USDC": str(Decimal(equity) - Decimal("10"))},
            "day_pnl_usdc_ex_flow_by_book": {"USDC": "10"},
        },
    )


def test_latest_ledger_snapshot_aggregates_multi_account(tmp_path) -> None:
    cfg_a = make_config(tmp_path, client_id="acct-a", client_secret="secret-a")
    cfg_b = make_config(tmp_path, client_id="acct-b", client_secret="secret-b")
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    ledger_a = tmp_path / "ledger-a"
    ledger_b = tmp_path / "ledger-b"
    _write_ledger_row(ledger_a, ts_ms=1_000, equity="5000", client_suffix="a")
    _write_ledger_row(ledger_b, ts_ms=2_000, equity="6000", client_suffix="b")
    accounts = [
        DashboardAccount("a", env_a, cfg_a, cfg_a.state_file, ledger_a),
        DashboardAccount("b", env_b, cfg_b, cfg_b.state_file, ledger_b),
    ]

    snap = frontend_server._latest_ledger_snapshot(accounts)

    assert snap["source"] == "ledger"
    assert snap["snapshot_ts_ms"] == 1_000
    assert Decimal(snap["portfolio"]["total_equity_usdc"]) == Decimal("11000")
    assert len(snap["accounts"]) == 2


def test_latest_ledger_snapshot_dedupes_shared_api_identity(tmp_path) -> None:
    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="dup", client_secret="same")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="dup", client_secret="same")
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    ledger_a = tmp_path / "ledger-a"
    ledger_b = tmp_path / "ledger-b"
    _write_ledger_row(ledger_a, ts_ms=1_000, equity="5000")
    _write_ledger_row(ledger_b, ts_ms=2_000, equity="9999")
    accounts = [
        DashboardAccount("a", env_a, cfg_a, cfg_a.state_file, ledger_a),
        DashboardAccount("b", env_b, cfg_b, cfg_b.state_file, ledger_b),
    ]

    snap = frontend_server._latest_ledger_snapshot(accounts)

    assert len(snap["accounts"]) == 2
    assert Decimal(snap["portfolio"]["total_equity_usdc"]) == Decimal("5000")


def test_portfolio_snapshot_endpoint_no_deribit(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=testnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json")

    def _no_deribit(*_args, **_kwargs):
        raise AssertionError("DeribitClient must not be used for /api/portfolio/snapshot")

    def _fake_snapshot(_accounts, **_kwargs):
        return {
            "source": "ledger",
            "snapshot_ts_ms": 5_000,
            "freshness_ms": 100,
            "portfolio": {"total_equity_usdc": "1234.56"},
            "accounts": [],
            "scheduler": {},
        }

    monkeypatch.setattr(frontend_server, "DeribitClient", _no_deribit)
    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "_latest_ledger_snapshot", _fake_snapshot)
    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )
    client = TestClient(app)

    response = client.get("/api/portfolio/snapshot")

    assert response.status_code == 200
    assert response.json()["portfolio"]["total_equity_usdc"] == "1234.56"


def test_dashboard_bundle_endpoint_returns_sections(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=testnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", client_id="cid", client_secret="sec")
    fake_status = {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {}}
    fake_summary = {"summary": {"realized_pnl_usdc": "50"}, "recent_closed_trades": []}
    aggregate_calls = {"status": 0, "groups": 0, "summary": 0}

    def _fake_status(*_args, **_kwargs):
        aggregate_calls["status"] += 1
        return fake_status

    def _fake_groups(*_args, **_kwargs):
        aggregate_calls["groups"] += 1
        return fake_groups

    def _fake_summary(*_args, **_kwargs):
        aggregate_calls["summary"] += 1
        return fake_summary

    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "_aggregate_status", _fake_status)
    monkeypatch.setattr(frontend_server, "_aggregate_groups", _fake_groups)
    monkeypatch.setattr(frontend_server, "_aggregate_realized_summary", _fake_summary)
    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )
    client = TestClient(app)

    response = client.get("/api/dashboard_bundle")

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["portfolio"]["total_equity_usdc"] == "1000"
    assert body["groups"]["closed"] == []
    assert body["realized_summary"]["summary"]["realized_pnl_usdc"] == "50"
    assert aggregate_calls == {"status": 1, "groups": 1, "summary": 1}

    cached = client.get("/api/dashboard_bundle")
    assert cached.status_code == 200
    assert aggregate_calls == {"status": 1, "groups": 1, "summary": 1}


def test_dashboard_bundle_sections_subset(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=testnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", client_id="cid", client_secret="sec")
    fake_status = {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {}}
    fake_summary = {"summary": {"realized_pnl_usdc": "50"}, "recent_closed_trades": []}
    aggregate_calls = {"status": 0, "groups": 0, "summary": 0}

    def _fake_status(*_args, **_kwargs):
        aggregate_calls["status"] += 1
        return fake_status

    def _fake_groups(*_args, **_kwargs):
        aggregate_calls["groups"] += 1
        return fake_groups

    def _fake_summary(*_args, **_kwargs):
        aggregate_calls["summary"] += 1
        return fake_summary

    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "_aggregate_status", _fake_status)
    monkeypatch.setattr(frontend_server, "_aggregate_groups", _fake_groups)
    monkeypatch.setattr(frontend_server, "_aggregate_realized_summary", _fake_summary)
    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )
    client = TestClient(app)

    response = client.get("/api/dashboard_bundle?sections=status,groups")

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "groups" in body
    assert "realized_summary" not in body
    assert aggregate_calls == {"status": 1, "groups": 1, "summary": 0}

    bad = client.get("/api/dashboard_bundle?sections=nope")
    assert bad.status_code == 400


def test_dashboard_bundle_endpoint_requires_private_creds(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=testnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", client_id="", client_secret="")
    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )
    client = TestClient(app)

    response = client.get("/api/dashboard_bundle")

    assert response.status_code == 401


def test_health_dashboard_strategies_from_accounts_toml(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config/shared/strategies").mkdir(parents=True)
    investor = tmp_path / "config/investors/alpha"
    accounts_dir = investor / "accounts"
    accounts_dir.mkdir(parents=True)
    account_env = accounts_dir / ".env.covered_call"
    account_env.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=covered_call",
                "STATE_FILE=.state/alpha/covered_call.json",
                "DERIBIT_CLIENT_ID=cid",
                "DERIBIT_CLIENT_SECRET=sec",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "covered_call"\nstrategy = "covered_call"\nenabled = true\n',
                '[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\nenabled = false\n',
            ]
        ),
        encoding="utf-8",
    )
    cfg = make_config(
        tmp_path,
        state_file=tmp_path / "bot.json",
        option_strategy="covered_call",
        client_id="cid",
        client_secret="sec",
    )
    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    app = frontend_server.create_app(
        env_file=account_env,
        account_env_files=(account_env,),
        enable_scheduler=False,
    )
    client = TestClient(app)

    body = client.get("/api/health").json()

    assert body["dashboard_strategies"] == ["covered_call"]
    assert body["investor_id"] == "alpha"


def test_investor_html_injects_dashboard_strategies(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config/shared/strategies").mkdir(parents=True)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/investor.html").write_text(
        "<!doctype html><html><head></head><body>ok</body></html>",
        encoding="utf-8",
    )
    investor = tmp_path / "config/investors/alpha"
    accounts_dir = investor / "accounts"
    accounts_dir.mkdir(parents=True)
    account_env = accounts_dir / ".env.covered_call"
    account_env.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=covered_call",
                "STATE_FILE=.state/alpha/covered_call.json",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "covered_call"\nstrategy = "covered_call"\nenabled = true\n',
                '[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\nenabled = false\n',
            ]
        ),
        encoding="utf-8",
    )
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", option_strategy="covered_call")
    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    app = frontend_server.create_app(
        env_file=account_env,
        account_env_files=(account_env,),
        enable_scheduler=False,
        investor_portal=True,
    )
    client = TestClient(app)

    html = client.get("/investor.html").text

    assert 'window.__DASHBOARD_STRATEGIES__=["covered_call"]' in html


def test_dashboard_strategies_fallback_to_loaded_accounts(tmp_path, monkeypatch) -> None:
    from deribit_engine.frontend_server.helpers import _dashboard_strategies

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=testnet\n", encoding="utf-8")
    cfg = make_config(
        tmp_path,
        state_file=tmp_path / "bot.json",
        option_strategy="bull_put_spread",
        client_id="cid",
        client_secret="sec",
    )
    assert _dashboard_strategies(
        investor_id=None,
        repo_root=None,
        accounts=[
            DashboardAccount(
                name="bull_put",
                env_file=env_file,
                config=cfg,
                state_path=Path(cfg.state_file),
                ledger_root=tmp_path / "ledger",
            )
        ],
    ) == ["bull_put_spread"]


def test_aggregate_status_fetches_accounts_in_parallel(tmp_path, monkeypatch) -> None:
    import threading
    import time

    from deribit_engine.engine import ExchangePrefetch

    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="a", client_secret="1")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="b", client_secret="2")
    cfg_c = make_config(tmp_path, option_strategy="bull_put_spread", client_id="c", client_secret="3")
    accounts = [
        DashboardAccount("a", tmp_path / "a.env", cfg_a, cfg_a.state_file, tmp_path / "la"),
        DashboardAccount("b", tmp_path / "b.env", cfg_b, cfg_b.state_file, tmp_path / "lb"),
        DashboardAccount("c", tmp_path / "c.env", cfg_c, cfg_c.state_file, tmp_path / "lc"),
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
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    class SlowBot:
        def fetch_exchange_prefetch(self) -> ExchangePrefetch:
            return prefetch

        def status_with_exchange_prefetch(self, _prefetch: ExchangePrefetch, **_kwargs) -> dict:
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.12)
            with lock:
                in_flight -= 1
            return {
                "env": "testnet",
                "portfolio": {
                    "total_equity_usdc": Decimal("1000"),
                    "equity_by_book": {"USDC": Decimal("1000")},
                },
                "underlying_index_usd": {},
                "accounts": {"USDC": {"equity": "1000"}},
                "trade_groups": [],
                "open_orders": [],
                "positions": [],
                "trade_group_count": 0,
            }

    monkeypatch.setattr(frontend_server, "_bot_for_account", lambda account, require_private=True: SlowBot())
    cache = frontend_server._TtlCache(15.0)

    started = time.monotonic()
    frontend_server._aggregate_status(accounts, exchange_prefetch_cache=cache)
    elapsed = time.monotonic() - started

    assert max_in_flight >= 2
    assert elapsed < 0.30


def test_aggregate_groups_fetches_accounts_in_parallel(tmp_path, monkeypatch) -> None:
    import threading
    import time

    from deribit_engine.engine import ExchangePrefetch

    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="a", client_secret="1")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="b", client_secret="2")
    cfg_c = make_config(tmp_path, option_strategy="bull_put_spread", client_id="c", client_secret="3")
    accounts = [
        DashboardAccount("a", tmp_path / "a.env", cfg_a, cfg_a.state_file, tmp_path / "la"),
        DashboardAccount("b", tmp_path / "b.env", cfg_b, cfg_b.state_file, tmp_path / "lb"),
        DashboardAccount("c", tmp_path / "c.env", cfg_c, cfg_c.state_file, tmp_path / "lc"),
    ]
    for account in accounts:
        StrategyStateStore(account.state_path).save(StrategyState())

    prefetch = ExchangePrefetch(
        summaries={},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": [], "ETH": []},
    )
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def slow_enrich(_bot, _payload, *, exchange_prefetch=None) -> None:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.12)
        with lock:
            in_flight -= 1

    monkeypatch.setattr(frontend_server, "_enrich_groups_payload_open_unrealized", slow_enrich)
    monkeypatch.setattr(frontend_server, "_bot_for_account", lambda account, require_private=True: object())
    monkeypatch.setattr(
        frontend_server,
        "_prefetch_all_accounts",
        lambda _accounts, cache: {frontend_server._live_api_identity(account): prefetch for account in accounts},
    )
    cache = frontend_server._TtlCache(15.0)

    started = time.monotonic()
    frontend_server._aggregate_groups(accounts, exchange_prefetch_cache=cache)
    elapsed = time.monotonic() - started

    assert max_in_flight >= 2
    assert elapsed < 0.30


def test_aggregate_stress_reuses_prefetch_without_account_refetch(tmp_path, monkeypatch) -> None:
    from deribit_engine.current_stress import CurrentStressResult
    from deribit_engine.engine import ExchangePrefetch

    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="a", client_secret="1")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="b", client_secret="2")
    accounts = [
        DashboardAccount("a", tmp_path / "a.env", cfg_a, cfg_a.state_file, tmp_path / "la"),
        DashboardAccount("b", tmp_path / "b.env", cfg_b, cfg_b.state_file, tmp_path / "lb"),
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
    stress_calls = {"prefetch": 0, "live": 0}

    def _fake_prefetch(config, *_args, **_kwargs):
        result = CurrentStressResult(
            generated_at="now",
            option_strategy=config.option_strategy,
            strategy_analysis={"label": config.option_strategy},
            index_by_ccy={"USDC": Decimal("1")},
            equity_usdc_by_book={"USDC": Decimal("1000")},
            positions=[],
            scenarios=[],
            notes=[],
        )
        stress_calls["prefetch"] += 1
        return result

    def _blocked_live(*_args, **_kwargs):
        stress_calls["live"] += 1
        raise AssertionError("compute_current_stress should not run when prefetch cache is warm")

    monkeypatch.setattr(frontend_server, "compute_stress_from_prefetch", _fake_prefetch)
    monkeypatch.setattr(frontend_server, "compute_current_stress", _blocked_live)
    monkeypatch.setattr(
        frontend_server,
        "_prefetch_all_accounts",
        lambda _accounts, cache: {frontend_server._live_api_identity(account): prefetch for account in accounts},
    )
    cache = frontend_server._TtlCache(15.0)

    payload = frontend_server._aggregate_stress(
        accounts,
        shocks=[Decimal("-0.10")],
        exchange_prefetch_cache=cache,
    )

    assert stress_calls == {"prefetch": 2, "live": 0}
    assert payload["option_strategy"] == "multi_account"
    assert len(payload["strategy_stresses"]) == 2
