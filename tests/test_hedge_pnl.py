from __future__ import annotations

from decimal import Decimal

from deribit_engine.hedge_pnl import merge_hedge_pnl_summaries, summarize_hedge_pnl_for_scope
from deribit_engine.trade_journal import (
    TradeJournalStore,
    ingest_engine_action,
    journal_db_path_for_state,
    scope_key_for_state,
)


def test_ingest_hedge_position_reconcile(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(journal_db_path_for_state(state_file))
    scope = scope_key_for_state(state_file)

    action = {
        "action": "hedge_position_reconcile",
        "currency": "ETH",
        "response": {
            "trades": [
                {
                    "trade_id": "h-1",
                    "order_id": "o-h1",
                    "instrument_name": "ETH_USDC-PERPETUAL",
                    "direction": "buy",
                    "amount": "0.1",
                    "price": "2000",
                    "fee": "0.5",
                    "fee_currency": "USDC",
                    "profit_loss": "-1.25",
                    "timestamp": 1000,
                    "label": "naked_short-hedge-eth-position",
                }
            ]
        },
    }
    assert ingest_engine_action(store, scope_key=scope, action=action) == 1
    rows = store.list_executions(scope, limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "hedge"
    assert rows[0]["source_action"] == "hedge_position_reconcile"


def test_summarize_hedge_pnl_for_scope(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(journal_db_path_for_state(state_file))
    scope = scope_key_for_state(state_file)

    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="BTC_USDC-PERPETUAL",
        direction="sell",
        amount=Decimal("0.01"),
        price=Decimal("70000"),
        fee_usdc=Decimal("0.3"),
        label="naked_short-hedge-btc-position",
        trade_id="t1",
        extra={"profit_loss": "-0.5"},
    )
    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="BTC_USDC-PERPETUAL",
        direction="buy",
        amount=Decimal("0.01"),
        price=Decimal("70100"),
        fee_usdc=Decimal("0.3"),
        label="naked_short-hedge-btc-position",
        trade_id="t2",
        extra={"profit_loss": "-0.2"},
    )

    summary = summarize_hedge_pnl_for_scope(store, scope)
    assert summary["trade_count"] == 2
    assert Decimal(summary["realized_pnl_usdc"]) == Decimal("-0.7")
    assert Decimal(summary["fees_usdc"]) == Decimal("0.6")
    assert Decimal(summary["net_pnl_usdc"]) == Decimal("-1.3")
    assert "BTC" in summary["by_currency"]


def test_merge_hedge_pnl_summaries():
    merged = merge_hedge_pnl_summaries(
        [
            {
                "trade_count": 2,
                "realized_pnl_usdc": "-1.0",
                "fees_usdc": "0.4",
                "net_pnl_usdc": "-1.4",
                "by_currency": {
                    "BTC": {
                        "trade_count": 2,
                        "realized_pnl_usdc": "-1.0",
                        "fees_usdc": "0.4",
                        "net_pnl_usdc": "-1.4",
                    }
                },
            },
            {
                "trade_count": 1,
                "realized_pnl_usdc": "-0.5",
                "fees_usdc": "0.1",
                "net_pnl_usdc": "-0.6",
                "by_currency": {
                    "ETH": {
                        "trade_count": 1,
                        "realized_pnl_usdc": "-0.5",
                        "fees_usdc": "0.1",
                        "net_pnl_usdc": "-0.6",
                    }
                },
            },
        ]
    )
    assert merged["trade_count"] == 3
    assert Decimal(merged["net_pnl_usdc"]) == Decimal("-2.0")


def test_hedge_performance_adjustments_window(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(journal_db_path_for_state(state_file))
    scope = scope_key_for_state(state_file)

    now_ms = 10_000_000_000_000
    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="BTC_USDC-PERPETUAL",
        direction="sell",
        amount=Decimal("0.01"),
        price=Decimal("70000"),
        fee_usdc=Decimal("0.3"),
        label="naked_short-hedge-btc-position",
        trade_id="t-old",
        extra={"profit_loss": "-1.0"},
        ts_ms=1_000,
    )
    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="BTC_USDC-PERPETUAL",
        direction="buy",
        amount=Decimal("0.01"),
        price=Decimal("70100"),
        fee_usdc=Decimal("0.3"),
        label="naked_short-hedge-btc-position",
        trade_id="t-new",
        extra={"profit_loss": "-0.2"},
        ts_ms=now_ms - 1_000,
    )

    from deribit_engine.hedge_pnl import hedge_performance_adjustments

    lifetime, window = hedge_performance_adjustments(
        [state_file],
        window_days=30,
        now_ms=now_ms,
    )
    assert lifetime == Decimal("-1.8")
    assert window == Decimal("-0.5")


def test_summarize_hedge_pnl_includes_liquidation_without_label(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(journal_db_path_for_state(state_file))
    scope = scope_key_for_state(state_file)

    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="ETH_USDC-PERPETUAL",
        direction="buy",
        amount=Decimal("3.284"),
        price=Decimal("1523.25"),
        fee_usdc=Decimal("37.5176"),
        label="",
        trade_id="liq-1",
        extra={"profit_loss": "4.2962", "order_type": "liquidation", "hedge_book_perp": True},
    )
    store.record_fill(
        scope_key=scope,
        event_type="hedge",
        source_action="backfill_api_hedge",
        instrument_name="ETH_USDC-PERPETUAL",
        direction="buy",
        amount=Decimal("1"),
        price=Decimal("1529"),
        fee_usdc=Decimal("0"),
        label="",
        trade_id="recv-1",
        extra={"profit_loss": "-4.4418", "hedge_book_perp": True},
    )

    summary = summarize_hedge_pnl_for_scope(store, scope)
    assert summary["trade_count"] == 2
    assert Decimal(summary["realized_pnl_usdc"]) == Decimal("-0.1456")
    assert Decimal(summary["fees_usdc"]) == Decimal("37.5176")
    assert Decimal(summary["net_pnl_usdc"]) == Decimal("-37.6632")
