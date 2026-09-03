from decimal import Decimal

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot

_NOBID_ROW = {
    "instrument_name": "BTC-NOBID",
    "bid_price": "0",
    "ask_price": "0.01",
    "mark_price": "0.005",
    "underlying_price": "70000",
    "open_interest": "5",
}
_LIVE_ROW = {
    "instrument_name": "BTC-LIVE",
    "bid_price": "0.003",
    "ask_price": "0.0034",
    "mark_price": "0.0032",
    "underlying_price": "70000",
    "open_interest": "60",
}


class _SummaryClient(FakeClient):
    def __init__(self, summary_rows, **kwargs):
        super().__init__(**kwargs)
        self._summary_rows = summary_rows
        self.order_book_calls: list[str] = []

    def get_book_summary_by_currency(self, currency, *, kind="option"):
        return list(self._summary_rows.get(currency.upper(), []))

    def get_order_book(self, instrument_name, *, depth=1):
        self.order_book_calls.append(instrument_name)
        return super().get_order_book(instrument_name, depth=depth)


def test_prefetch_seeds_no_bid_strikes_only(tmp_path):
    client = _SummaryClient({"BTC": [_NOBID_ROW, _LIVE_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path, scan_book_summary_prefilter=True), client)
    cache = {}

    engine._prefetch_scan_book_summaries({"BTC": [object()]}, cache)

    assert "BTC-NOBID" in cache
    assert cache["BTC-NOBID"].best_bid_price == Decimal("0")
    # A strike that still has a bid must fall through to a real order-book fetch.
    assert "BTC-LIVE" not in cache
    # The summary scan itself must not trigger any per-instrument fetch.
    assert client.order_book_calls == []


def test_prefetch_disabled_by_default(tmp_path):
    """Opt-in: the whole-chain summary is a heavy endpoint, and every investor
    on this host shares one IP for public rate limits."""
    client = _SummaryClient({"BTC": [_NOBID_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path), client)
    cache = {}

    engine._prefetch_scan_book_summaries({"BTC": [object()]}, cache)

    assert cache == {}


def test_prefetch_force_overrides_the_flag(tmp_path):
    """force=True is for callers where the batch summary *replaces*
    per-instrument fetches (the dashboard regime probe)."""
    client = _SummaryClient({"BTC": [_NOBID_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path), client)
    cache = {}

    engine._prefetch_scan_book_summaries({"BTC": [object()]}, cache, force=True)

    assert "BTC-NOBID" in cache


def test_prefetch_skips_currency_without_markets(tmp_path):
    client = _SummaryClient({"BTC": [_NOBID_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path, scan_book_summary_prefilter=True), client)
    cache = {}

    engine._prefetch_scan_book_summaries({"BTC": []}, cache)

    assert cache == {}


def test_seeded_no_bid_skips_order_book_fetch(tmp_path):
    client = _SummaryClient({"BTC": [_NOBID_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path, scan_book_summary_prefilter=True), client)
    cache = {}
    engine._prefetch_scan_book_summaries({"BTC": [object()]}, cache)

    book = engine._get_orderbook("BTC-NOBID", cache)

    assert book.best_bid_price == Decimal("0")
    assert client.order_book_calls == []


def _closed_covered_call_group(index: int):
    from deribit_engine.models import TradeGroup

    return TradeGroup.from_dict(
        {
            "group_id": f"g{index}",
            "currency": "BTC",
            "short_instrument_name": "BTC-28MAR25-90000-C",
            "short_label": f"cc-{index}",
            "status": "closed",
            "strategy": "covered_call",
            "option_type": "call",
            "collateral_currency": "BTC",
            "quantity": "0.1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "short_strike": "90000",
            "entry_credit": "30",
            "original_entry_credit": "30",
            "max_loss": "1000",
            "regime_at_entry": "normal",
        }
    )


def test_state_repair_batches_currency_trade_lookups(tmp_path):
    """The closed-group repair pass must batch its exchange lookups: one fetch
    per currency, not one per closed group."""
    client = _SummaryClient({})
    calls: list[str] = []

    def fake_user_trades(currency, *, kind="spot", count=100, historical=True):
        calls.append(currency)
        return {"trades": []}

    client.get_user_trades_by_currency = fake_user_trades
    engine = DeribitOptionTrialBot(
        make_config(tmp_path, option_strategy="covered_call", order_label_prefix="cc"),
        client,
    )
    state = engine.state_store.load()
    state.groups = [_closed_covered_call_group(i) for i in range(8)]

    engine._repair_reconciled_bot_income_exits_in_state(state)

    assert calls.count("BTC") <= 1, f"expected one batched fetch, got {len(calls)}"
