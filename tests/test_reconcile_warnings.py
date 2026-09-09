"""Reconcile must log (not swallow) failures that leave a PnL component unknown."""

from __future__ import annotations

import logging
from decimal import Decimal

from conftest import FakeClient, make_config
from test_engine import _build_group

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exceptions import TransientExchangeError

SHORT = "BTC_USDC-14APR30-63000-P"


class _BooksDownClient(FakeClient):
    """Every public market-data read fails: no book, no instrument metadata, no index."""

    def get_order_book(self, instrument_name, *args, **kwargs):
        raise TransientExchangeError("public/get_order_book failed after retries: HTTP 502")

    def get_instrument(self, instrument_name, *args, **kwargs):
        raise TransientExchangeError("public/get_instrument failed after retries: HTTP 502")

    def get_instruments(self, *args, **kwargs):
        raise TransientExchangeError("public/get_instruments failed after retries: HTTP 502")

    def get_index_price(self, *args, **kwargs):
        raise TransientExchangeError("public/get_index_price failed after retries: HTTP 502")


def test_expired_group_close_debit_fallback_is_logged_not_silent(tmp_path, caplog):
    client = _BooksDownClient()
    engine = DeribitOptionTrialBot(make_config(tmp_path, option_markets_profile="linear_usdc"), client)
    group = _build_group(short_instrument_name=SHORT, dte_days=-1)
    group.current_debit = Decimal("7.5")

    with caplog.at_level(logging.WARNING, logger="deribit_engine"):
        debit = engine._estimate_reconcile_close_debit(group, {}, markets_by_currency={})

    # Behavior unchanged: the stale pre-close mark is still the last resort...
    assert debit == Decimal("7.5")
    # ...but every swallowed step now leaves a trace for the operator.
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("mark-based close debit failed" in m for m in messages)
    assert any("no index price for group=0001" in m for m in messages)
    assert any("falls back to stale current_debit=7.5" in m and "realized PnL may be inaccurate" in m for m in messages)


def test_expired_group_without_any_debit_source_returns_none_with_warning(tmp_path, caplog):
    client = _BooksDownClient()
    engine = DeribitOptionTrialBot(make_config(tmp_path, option_markets_profile="linear_usdc"), client)
    group = _build_group(short_instrument_name=SHORT, dte_days=-1)
    group.current_debit = Decimal("-1")

    with caplog.at_level(logging.WARNING, logger="deribit_engine"):
        debit = engine._estimate_reconcile_close_debit(group, {}, markets_by_currency={})

    assert debit is None
    assert any("mark-based close debit failed" in r.getMessage() for r in caplog.records)
