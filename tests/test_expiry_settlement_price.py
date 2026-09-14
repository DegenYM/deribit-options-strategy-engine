"""Expiry decisions use the price the exchange settled at, not the index at reconcile time.

After downtime the two can sit on opposite sides of the strike. A call that expired out of the
money, reconciled hours later with spot above the strike, used to have its coin sold as if it
had been called away; a put that expired out of the money, reconciled after a drop, used to be
treated as assigned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot

STRIKE = Decimal("80000")


def _expired_group():
    expiry = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    group = SimpleNamespace(
        group_id="g1",
        currency="BTC",
        short_strike=STRIKE,
        expiration_timestamp_ms=int(expiry.timestamp() * 1000),
        close_index_usd=Decimal("0"),
    )
    return group, expiry.strftime("%Y-%m-%d")


class _SettledClient(FakeClient):
    def __init__(self, settled: dict[str, Decimal], **kwargs):
        super().__init__(**kwargs)
        self._settled = settled

    def get_delivery_prices(self, index_name, *, days=400):
        return sorted(self._settled.items())


def _engine(tmp_path, client):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("BTC",),
        enable_short_put=False,
        enable_short_call=True,
    )
    return DeribitOptionTrialBot(config, client)


def test_a_call_that_expired_otm_is_not_called_away_after_a_rally(tmp_path):
    group, day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({day: Decimal("79000")}))
    with patch.object(engine, "_currency_index_price", return_value=Decimal("83000")):
        assert engine._covered_call_itm_from_cache(group, {}) is True  # what reconcile used to decide
        assert engine._covered_call_expired_itm(group, {}) is False


def test_a_call_that_expired_itm_is_called_away_even_after_a_drop(tmp_path):
    group, day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({day: Decimal("81000")}))
    with patch.object(engine, "_currency_index_price", return_value=Decimal("78000")):
        assert engine._covered_call_expired_itm(group, {}) is True


def test_no_settlement_price_for_that_day_falls_back_to_the_index(tmp_path):
    group, _day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({"2000-01-01": Decimal("1")}))
    with patch.object(engine, "_currency_index_price", return_value=Decimal("83000")):
        assert engine._covered_call_expired_itm(group, {}) is True


def test_a_client_without_delivery_prices_keeps_the_old_behaviour(tmp_path):
    group, _day = _expired_group()
    engine = _engine(tmp_path, FakeClient())
    with patch.object(engine, "_currency_index_price", return_value=Decimal("83000")):
        assert engine._covered_call_expired_itm(group, {}) is True


def test_a_put_that_expired_otm_is_not_assigned_after_a_drop(tmp_path):
    group, day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({day: Decimal("81000")}))
    assert engine._cash_secured_put_itm_from_cache(group, {}, Decimal("78000")) is True  # the old answer
    assert engine._cash_secured_put_expired_itm(group, {}, Decimal("78000")) is False


def test_a_put_that_expired_itm_is_assigned_even_after_a_rally(tmp_path):
    group, day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({day: Decimal("79000")}))
    assert engine._cash_secured_put_expired_itm(group, {}, Decimal("81000")) is True


def test_the_price_is_looked_up_by_the_expiry_day(tmp_path):
    group, day = _expired_group()
    engine = _engine(tmp_path, _SettledClient({"2000-01-01": Decimal("1"), day: Decimal("79000")}))
    assert engine._expiry_settlement_price(group) == Decimal("79000")
