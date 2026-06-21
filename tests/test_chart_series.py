from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_engine.frontend_server.helpers import (
    _append_ledger,
    _cumulative_pnl_series,
    _cumulative_spot_pnl_series,
    _equity_native_by_book_series,
    _spot_series_cache_key,
)
from deribit_engine.frontend_server.types import DashboardAccount, _TtlCache


def _dashboard_account(name: str, ledger_root: Path, *, client_id: str = "cid") -> DashboardAccount:
    return DashboardAccount(
        name=name,
        env_file=ledger_root / f"{name}.env",
        config=type(
            "Cfg",
            (),
            {
                "client_id": client_id,
                "client_secret": "sec",
                "env": "test",
                "option_strategy": "covered_call",
                "state_file": str(ledger_root / f"{name}.json"),
            },
        )(),
        state_path=ledger_root / f"{name}.json",
        ledger_root=ledger_root,
    )


def test_spot_series_cache_key_is_hashable_for_ttl_cache() -> None:
    spot = {"BTC": "105000", "ETH": "3500"}
    cache_key = ("cumulative_pnl_stable", (), _spot_series_cache_key(spot))
    cache = _TtlCache(60.0)
    assert cache.get_or_set(cache_key, lambda: {"ok": True}) == {"ok": True}


def test_cumulative_stable_pnl_uses_sweep_proceeds(tmp_path: Path) -> None:
    closed = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "38",
            "realized_pnl_collateral_native": "0.00048072",
            "profit_sweep_status": "filled",
            "profit_sweep_amount": "0.00048072",
            "profit_sweep_quote_proceeds_lifetime": "0.08355164",
            "closed_timestamp_ms": 1_700_000_000_000,
        },
        {
            "status": "closed",
            "collateral_currency": "USDC",
            "currency": "USDC",
            "realized_pnl": "10",
            "closed_timestamp_ms": 1_700_086_400_000,
        },
    ]
    series = _cumulative_pnl_series(closed, spot_index={"BTC": Decimal("120000")})
    total = Decimal(series["cumulative_total"][-1]["pnl_usdc"])
    assert total == Decimal("10.08355164")


def test_cumulative_spot_pnl_native_from_closed_groups() -> None:
    closed = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "38",
            "realized_pnl_collateral_native": "0.0005",
            "closed_timestamp_ms": 1_700_000_000_000,
        },
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "20",
            "realized_pnl_collateral_native": "0.0003",
            "closed_timestamp_ms": 1_700_086_400_000,
        },
        {
            "status": "closed",
            "collateral_currency": "USDC",
            "currency": "USDC",
            "realized_pnl": "10",
            "closed_timestamp_ms": 1_700_086_400_000,
        },
    ]
    series = _cumulative_spot_pnl_series(closed)
    btc_total = Decimal(series["cumulative_by_book"]["BTC"][-1]["pnl_native"])
    assert btc_total == Decimal("0.0008")
    assert "USDC" not in series["books"]
    assert series["realized_count"] == 2


def test_equity_native_by_book_series_aggregates_daily_native(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    account = _dashboard_account("main", ledger)
    day1 = int(datetime(2024, 6, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)
    day2 = int(datetime(2024, 6, 2, 12, 0, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": day1,
            "equity_native_by_book": {"BTC": "0.5", "ETH": "10", "USDC": "1000"},
            "equity_by_book": {"BTC": "30000", "ETH": "35000", "USDC": "1000"},
        },
    )
    _append_ledger(
        ledger,
        {
            "ts_ms": day2,
            "equity_native_by_book": {"BTC": "0.55", "ETH": "11", "USDC": "1100"},
            "equity_by_book": {"BTC": "33000", "ETH": "38500", "USDC": "1100"},
        },
    )

    series = _equity_native_by_book_series([account])
    btc_rows = series["series_by_book"]["BTC"]
    assert btc_rows[0]["date"] == "2024-06-01"
    assert Decimal(btc_rows[0]["equity_native"]) == Decimal("0.5")
    assert Decimal(btc_rows[1]["equity_native"]) == Decimal("0.55")
    assert Decimal(series["series_by_book"]["USDC"][1]["equity_native"]) == Decimal("1100")


def test_equity_native_series_treats_small_coin_book_as_native(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    account = _dashboard_account("main", ledger)
    ts = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": ts,
            "equity_by_book": {"BTC": "0.20455", "ETH": "2.0787", "USDC": "2637.74"},
            "index_btc_usd": "105000",
            "index_eth_usd": "3500",
        },
    )
    series = _equity_native_by_book_series([account])
    assert Decimal(series["series_by_book"]["BTC"][0]["equity_native"]) == Decimal("0.20455")
    assert Decimal(series["series_by_book"]["ETH"][0]["equity_native"]) == Decimal("2.0787")


def test_backfill_ledger_writes_equity_native_by_book(tmp_path: Path) -> None:
    from deribit_engine.frontend_ledger_backfill import backfill_ledger_equity_native

    ledger = tmp_path / "ledger"
    ledger.mkdir()
    ts = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": ts,
            "equity_by_book": {"BTC": "0.20455", "ETH": "2.0787", "USDC": "2637.74"},
            "index_btc_usd": "105000",
            "index_eth_usd": "3500",
        },
    )
    summary = backfill_ledger_equity_native(ledger, client=None, market_store=None, dry_run=False)
    assert summary.rows_updated == 1
    rows = list(ledger.glob("equity_*.jsonl"))
    row = json.loads(rows[0].read_text(encoding="utf-8").strip())
    assert row["equity_native_by_book"]["BTC"] == "0.20455"
    assert row["equity_native_by_book"]["USDC"] == "2637.74"


def test_equity_native_series_falls_back_to_usdc_from_equity_by_book(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    account = _dashboard_account("main", ledger)
    ts = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": ts,
            "equity_by_book": {"USDC": "2500"},
        },
    )
    series = _equity_native_by_book_series([account])
    assert Decimal(series["series_by_book"]["USDC"][0]["equity_native"]) == Decimal("2500")


def test_equity_native_series_derives_coin_book_from_usdc_equity_and_index(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    account = _dashboard_account("main", ledger)
    ts = int(datetime(2024, 6, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": ts,
            "equity_by_book": {"BTC": "70000", "ETH": "10000", "USDC": "1000"},
            "index_btc_usd": "70000",
            "index_eth_usd": "2500",
        },
    )
    series = _equity_native_by_book_series([account])
    assert Decimal(series["series_by_book"]["BTC"][0]["equity_native"]) == Decimal("1")
    assert Decimal(series["series_by_book"]["ETH"][0]["equity_native"]) == Decimal("4")
    assert Decimal(series["series_by_book"]["USDC"][0]["equity_native"]) == Decimal("1000")


def test_equity_native_series_ignores_stored_zero_when_usdc_book_equity_exists(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    account = _dashboard_account("main", ledger)
    ts = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    _append_ledger(
        ledger,
        {
            "ts_ms": ts,
            "equity_by_book": {"BTC": "105000"},
            "equity_native_by_book": {"BTC": "0"},
            "index_btc_usd": "70000",
        },
    )
    series = _equity_native_by_book_series([account])
    assert Decimal(series["series_by_book"]["BTC"][0]["equity_native"]) == Decimal("1.5")
