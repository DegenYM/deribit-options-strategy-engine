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
    client = _SummaryClient({"BTC": [_NOBID_ROW]})
    engine = DeribitOptionTrialBot(make_config(tmp_path), client)
    cache = {}

    engine._prefetch_scan_book_summaries({"BTC": [object()]}, cache)

    assert cache == {}


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
