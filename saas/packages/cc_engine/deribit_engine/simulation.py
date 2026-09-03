"""Historical market replay layer for engine-integrated backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .backtest import bs_delta, bs_price
from .backtest_data import BacktestDataClient, pick_nearest_value
from .config import BotConfig
from .models import OptionInstrument
from .utils import ONE, to_decimal


@dataclass
class HistoricalFeed:
    """Point-in-time market snapshot for simulation."""

    ts_ms: int
    index_by_currency: dict[str, Decimal] = field(default_factory=dict)
    dvol_by_currency: dict[str, Decimal] = field(default_factory=dict)
    instruments_by_currency: dict[str, list[OptionInstrument]] = field(default_factory=dict)
    assumed_spread_ratio: Decimal = Decimal("0.06")

    def spot(self, currency: str) -> Decimal:
        return self.index_by_currency.get(currency.upper(), Decimal("0"))

    def sigma(self, currency: str) -> Decimal:
        dvol = self.dvol_by_currency.get(currency.upper(), Decimal("0"))
        if dvol <= 0:
            return Decimal("0.5")
        return dvol / Decimal("100")

    def synthetic_order_book(self, instrument: OptionInstrument) -> dict[str, Any]:
        ccy = instrument.base_currency.upper()
        spot = float(self.spot(ccy))
        strike = float(instrument.strike)
        t_years = max(
            (instrument.expiration_timestamp_ms - self.ts_ms) / (365.0 * 86400000.0),
            1e-6,
        )
        sigma = float(self.sigma(ccy))
        side = (instrument.option_type or "put").lower()
        prem = bs_price(spot=spot, strike=strike, t_years=t_years, sigma=sigma, option_type=side)
        delta = bs_delta(spot=spot, strike=strike, t_years=t_years, sigma=sigma, option_type=side)
        prem_d = to_decimal(prem)
        spr = self.assumed_spread_ratio
        bid = prem_d * (ONE - spr / Decimal("2"))
        ask = prem_d * (ONE + spr / Decimal("2"))
        if ask <= 0:
            ask = prem_d
        if bid < 0:
            bid = Decimal("0")
        index = self.spot(ccy)
        return {
            "instrument_name": instrument.instrument_name,
            "best_bid_price": str(bid),
            "best_bid_amount": "1000000",
            "best_ask_price": str(ask),
            "best_ask_amount": "1000000",
            "mark_price": str(prem_d),
            "index_price": str(index),
            "mark_iv": str(self.dvol_by_currency.get(ccy, Decimal("0"))),
            "open_interest": "1000000",
            "greeks": {"delta": str(delta)},
        }


class SimulatedDeribitClient:
    """Minimal DeribitClient-compatible adapter backed by ``HistoricalFeed``."""

    def __init__(
        self,
        config: BotConfig,
        *,
        data: BacktestDataClient,
        feed: HistoricalFeed,
    ):
        self.config = config
        self._data = data
        self._feed = feed
        self._positions: list[dict[str, Any]] = []
        self._open_orders: list[dict[str, Any]] = []
        self._summaries: dict[str, dict[str, Any]] = {
            book: {
                "currency": book,
                "equity": str(config.reference_capital_usdc / Decimal("3")),
                "maintenance_margin": "0",
                "initial_margin": "0",
            }
            for book in ("BTC", "ETH", "USDC")
        }

    @property
    def feed(self) -> HistoricalFeed:
        return self._feed

    def advance_to(self, day: datetime) -> None:
        """Load index/DVOL/instruments for ``day`` into the active feed."""
        ts_ms = int(day.replace(tzinfo=UTC).timestamp() * 1000)
        self._feed.ts_ms = ts_ms
        start_ms = ts_ms - (365 * 86400000)
        for ccy in self.config.managed_currencies:
            c = ccy.upper()
            idx_name = f"{c.lower()}_usd"
            index_series = self._data.get_index_series(idx_name, range_name="all")
            spot_raw = pick_nearest_value(index_series, ts_ms=ts_ms)
            self._feed.index_by_currency[c] = to_decimal(spot_raw or 0)
            dvol_series = self._data.get_dvol_series(c, start_timestamp=start_ms, end_timestamp=ts_ms, resolution="1D")
            dvol_raw = pick_nearest_value(dvol_series, ts_ms=ts_ms)
            self._feed.dvol_by_currency[c] = to_decimal(dvol_raw or 0)
            self._feed.instruments_by_currency[c] = [
                inst
                for inst in self._data.list_option_instruments(c, include_expired=False)
                if inst.expiration_timestamp_ms > ts_ms
            ]

    def ping(self) -> dict[str, Any]:
        return {"version": "simulation"}

    def get_instrument(self, instrument_name: str) -> dict[str, Any]:
        for insts in self._feed.instruments_by_currency.values():
            for inst in insts:
                if inst.instrument_name == instrument_name:
                    return _instrument_to_dict(inst)
        raise KeyError(instrument_name)

    def get_open_orders(self, *, kind: str = "any") -> list[dict[str, Any]]:
        return list(self._open_orders)

    def get_positions(self, *, currency: str = "any", kind: str = "any") -> list[dict[str, Any]]:
        return list(self._positions)

    def get_account_summaries(self, *, extended: bool = False) -> list[dict[str, Any]]:
        return list(self._summaries.values())

    def get_user_trades_by_order(self, order_id: str) -> list[dict[str, Any]]:
        return []

    def get_order_state(self, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "order_state": "filled", "filled_amount": "0"}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.cancel(order_id)

    def close_position(self, instrument_name: str, *, order_type: str = "market") -> dict[str, Any]:
        return {"order": {"order_state": "filled", "instrument_name": instrument_name}}

    def place_order(self, **kwargs: Any) -> dict[str, Any]:
        direction = str(kwargs.get("direction") or "buy")
        if direction == "sell":
            return self.place_sell_order(**kwargs)
        return self.place_buy_order(**kwargs)

    def get_instruments(
        self,
        currency: str,
        *,
        kind: str = "option",
        expired: bool = False,
    ) -> list[dict[str, Any]]:
        if kind != "option":
            return []
        c = currency.upper()
        instruments = self._feed.instruments_by_currency.get(c, [])
        if expired:
            instruments = self._data.list_option_instruments(c, include_expired=True)
        return [_instrument_to_dict(inst) for inst in instruments]

    def get_order_book(self, instrument_name: str, *, depth: int = 1) -> dict[str, Any]:
        for insts in self._feed.instruments_by_currency.values():
            for inst in insts:
                if inst.instrument_name == instrument_name:
                    return self._feed.synthetic_order_book(inst)
        raise KeyError(instrument_name)

    def get_book_summary_by_currency(self, currency: str, *, kind: str = "option") -> list[dict[str, Any]]:
        if kind != "option":
            return []
        rows: list[dict[str, Any]] = []
        for inst in self._feed.instruments_by_currency.get(currency.upper(), []):
            book = self._feed.synthetic_order_book(inst)
            rows.append(
                {
                    "instrument_name": inst.instrument_name,
                    "bid_price": book.get("best_bid_price"),
                    "ask_price": book.get("best_ask_price"),
                    "mark_price": book.get("mark_price"),
                    "underlying_price": book.get("index_price"),
                    "mark_iv": book.get("mark_iv"),
                    "open_interest": book.get("open_interest"),
                }
            )
        return rows

    def get_index_price(self, index_name: str) -> dict[str, Any]:
        ccy = index_name.split("_")[0].upper()
        price = self._feed.spot(ccy)
        return {"index_price": str(price)}

    def get_volatility_index_data(
        self,
        currency: str,
        *,
        start_timestamp: int,
        end_timestamp: int,
        resolution: str = "1D",
    ) -> dict[str, Any]:
        rows = self._data.get_dvol_series(
            currency.upper(),
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            resolution=resolution,
        )
        return {"data": [[ts, 0, 0, 0, float(v)] for ts, v in rows]}

    def get_index_chart_data(self, index_name: str, *, range_name: str = "1d") -> list[list[Any]]:
        series = self._data.get_index_series(index_name, range_name="all")
        return [[ts, 0, 0, 0, float(v)] for ts, v in series]

    def get_account_summary(self, currency: str) -> dict[str, Any]:
        return dict(self._summaries.get(currency.upper(), {"currency": currency.upper(), "equity": "0"}))

    def get_open_orders_by_currency(self, currency: str) -> list[dict[str, Any]]:
        return [o for o in self._open_orders if o.get("currency") == currency.upper()]

    def place_sell_order(self, **kwargs: Any) -> dict[str, Any]:
        return _instant_fill_response(kwargs, direction="sell")

    def place_buy_order(self, **kwargs: Any) -> dict[str, Any]:
        return _instant_fill_response(kwargs, direction="buy")

    def cancel(self, order_id: str) -> dict[str, Any]:
        self._open_orders = [o for o in self._open_orders if o.get("order_id") != order_id]
        return {"order_id": order_id, "order_state": "cancelled"}


def _instrument_to_dict(inst: OptionInstrument) -> dict[str, Any]:
    return {
        "instrument_name": inst.instrument_name,
        "base_currency": inst.base_currency,
        "quote_currency": inst.quote_currency,
        "settlement_currency": inst.settlement_currency,
        "instrument_type": inst.instrument_type,
        "tick_size": str(inst.tick_size),
        "min_trade_amount": str(inst.min_trade_amount),
        "contract_size": str(inst.contract_size),
        "option_type": inst.option_type,
        "expiration_timestamp": inst.expiration_timestamp_ms,
        "strike": str(inst.strike),
        "instrument_state": inst.instrument_state or "open",
    }


def _instant_fill_response(kwargs: dict[str, Any], *, direction: str) -> dict[str, Any]:
    amount = kwargs.get("amount") or kwargs.get("contracts") or "1"
    price = kwargs.get("price") or "0"
    return {
        "order": {
            "order_id": f"sim-{direction}-{kwargs.get('label', 'x')}",
            "order_state": "filled",
            "filled_amount": str(amount),
            "average_price": str(price),
            "price": str(price),
            "direction": direction,
            "instrument_name": kwargs.get("instrument_name"),
        },
        "trades": [],
    }


def run_engine_backtest(
    config: BotConfig,
    data: BacktestDataClient,
    *,
    start: datetime,
    end: datetime,
    cycles_per_day: int = 1,
) -> dict[str, Any]:
    """Drive ``DeribitOptionTrialBot`` over historical days using ``SimulatedDeribitClient``."""
    from .engine.bot import DeribitOptionTrialBot

    feed = HistoricalFeed(ts_ms=int(start.replace(tzinfo=UTC).timestamp() * 1000))
    sim = SimulatedDeribitClient(config, data=data, feed=feed)
    bot = DeribitOptionTrialBot(config, sim)
    day = start
    results: list[dict[str, Any]] = []
    while day <= end:
        sim.advance_to(day)
        for _ in range(cycles_per_day):
            results.append(bot.run(cycles=1, live=False))
        day += timedelta(days=1)
    return {"days": len(results), "cycles": results}
