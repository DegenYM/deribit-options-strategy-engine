from decimal import Decimal
from pathlib import Path

from cc_engine.settings import CoveredCallSettings
from cc_engine.snapshot import load_worker_snapshot, performance_from_state
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore


def _settings(tmp_path: Path) -> CoveredCallSettings:
    return CoveredCallSettings(
        tenant_id="11111111-2222-3333-4444-555555555555",
        risk_tier="low",
        coins=("BTC",),
        profit_sweep=False,
        live=False,
        state_dir=tmp_path,
        client_id="cid",
        client_secret="csecret",
    )


def _group(**overrides) -> TradeGroup:
    payload = dict(
        group_id="1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("1"),
        entry_timestamp_ms=1_700_000_000_000,
        expiration_timestamp_ms=1_700_900_000_000,
        short_instrument_name="BTC-29DEC23-100000-C",
        short_strike=Decimal("100000"),
        entry_credit=Decimal("0.01"),
        original_entry_credit=Decimal("0.01"),
        max_loss=Decimal("1"),
        regime_at_entry="normal",
        option_type="call",
        strategy="covered_call",
        status="open",
        entry_index_usd=Decimal("100000"),
    )
    payload.update(overrides)
    return TradeGroup(**payload)


def test_performance_from_empty_state():
    perf = performance_from_state(StrategyState())
    assert perf["has_data"] is False
    assert perf["total_equity_usdc"] is None
    assert perf["open_credit_usdc"] == 0.0
    assert "不是收益承諾" in perf["disclaimer_zh"]


def test_snapshot_performance_kpis(tmp_path: Path):
    settings = _settings(tmp_path)
    opened = _group()
    closed = _group(
        group_id="2",
        status="closed",
        realized_pnl=Decimal("200"),
        entry_credit=Decimal("0.02"),
        closed_timestamp_ms=1_700_086_400_000,
    )
    state = StrategyState(groups=[opened, closed], last_equity_usdc=Decimal("50000"))
    StrategyStateStore(settings.state_file).save(state)
    snap = load_worker_snapshot(settings)
    perf = snap["performance"]
    assert perf["has_data"] is True
    assert perf["total_equity_usdc"] == 50000.0
    assert perf["lifetime_pnl_usdc"] == 200.0
    assert perf["open_credit_usdc"] == 1000.0
    assert perf["open_credit_native_by_book"]["BTC"] == 0.01
    assert perf["lifetime_pnl_native_by_book"]["BTC"] == 0.002
    assert snap["closed_groups"][0]["realized_pnl_native"] == "0.002"
    assert perf["win_rate"] == 1.0
    assert perf["avg_holding_days"] == 1.0
    assert perf["open_count"] == 1
    assert perf["closed_count"] == 1
    assert snap["open_groups"][0]["entry_credit_usdc"] == "1000.00"
    assert snap["closed_groups"][0]["realized_pnl"] == "200"
    assert perf["lifetime_apr"] is not None
