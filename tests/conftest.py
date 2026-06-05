from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deribit_engine.book import Book
from deribit_engine.config import BotConfig


def future_expiry(days: int) -> int:
    return int((datetime.now(tz=UTC) + timedelta(days=days)).timestamp() * 1000)


def make_config(tmp_path: Path, **overrides) -> BotConfig:
    values = dict(
        env="mainnet",
        client_id="id",
        client_secret="secret",
        option_strategy="naked_short",
        option_markets_profile="all",
        managed_currencies=("BTC", "ETH"),
        top_n=5,
        reference_capital_usdc=Decimal("1000"),
        target_portfolio_apr=Decimal("0.20"),
        entry_dte_min=7,
        entry_dte_max=24,
        short_put_delta_min=Decimal("0.08"),
        short_put_delta_max=Decimal("0.14"),
        preferred_short_put_delta_min=Decimal("0.10"),
        preferred_short_put_delta_max=Decimal("0.12"),
        put_otm_min=Decimal("0.08"),
        put_otm_max=Decimal("0.14"),
        min_liquid_expiries_required=2,
        halt_open_max_loss_pct=Decimal("0.45"),
        tp_capture_pct=Decimal("0.60"),
        enable_early_exit=True,
        early_exit_remaining_apr=Decimal("0.08"),
        early_exit_min_profit_capture=Decimal("0.25"),
        early_exit_max_spread_ratio=Decimal("0.05"),
        time_exit_dte=5,
        soft_defense_delta=Decimal("0.25"),
        hard_defense_delta=Decimal("0.35"),
        soft_defense_loss_pct=Decimal("0.35"),
        hard_stop_loss_pct=Decimal("0.55"),
        cooldown_hours=24,
        poll_seconds_normal=15,
        poll_seconds_stress=5,
        short_entry_wait_seconds=1,
        order_poll_seconds=1,
        option_fee_rate=Decimal("0.0003"),
        option_fee_cap_rate=Decimal("0.125"),
        option_fee_discount_rate=Decimal("0"),
        option_fee_discount_months=6,
        option_fee_discount_anchor="registration",
        option_fee_discount_registration_ms=0,
        exit_buffer_ratio=Decimal("0.03"),
        index_drawdown_elevated_pct=Decimal("0.04"),
        index_drawdown_crisis_pct=Decimal("0.06"),
        dvol_elevated_multiplier=Decimal("1.25"),
        dvol_crisis_multiplier=Decimal("1.60"),
        halt_drawdown_pct=Decimal("0.03"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
        hard_derisk_maintenance_margin_ratio=Decimal("0.12"),
        hard_derisk_on_crisis_open_group=False,
        enable_perp_hedge=False,
        soft_hedge_delta_cap_pct=Decimal("0.10"),
        hard_hedge_delta_cap_pct=Decimal("0.05"),
        max_concurrent_groups=2,
        max_groups_per_currency=1,
        recovery_normal_cycles=3,
        order_label_prefix="trial",
        request_timeout_seconds=20,
        state_file=tmp_path / "strategy_state.json",
        min_net_apr=Decimal("0.12"),
        target_net_apr_min=Decimal("0.15"),
        target_net_apr_max=Decimal("0.20"),
        btc_put_delta_min=Decimal("0.08"),
        btc_put_delta_max=Decimal("0.12"),
        eth_put_delta_min=Decimal("0.06"),
        eth_put_delta_max=Decimal("0.10"),
        btc_put_otm_min=Decimal("0.10"),
        btc_put_otm_max=Decimal("0.18"),
        eth_put_otm_min=Decimal("0.12"),
        eth_put_otm_max=Decimal("0.20"),
        btc_preferred_put_delta_min=Decimal("0.09"),
        btc_preferred_put_delta_max=Decimal("0.11"),
        eth_preferred_put_delta_min=Decimal("0.07"),
        eth_preferred_put_delta_max=Decimal("0.09"),
        btc_preferred_otm_min=Decimal("0.12"),
        btc_preferred_otm_max=Decimal("0.16"),
        eth_preferred_otm_min=Decimal("0.14"),
        eth_preferred_otm_max=Decimal("0.18"),
        enable_naked_topup=False,
        enable_adopt_exchange_positions=True,
        enable_short_put=True,
        enable_short_call=False,
        short_call_delta_min=Decimal("0.08"),
        short_call_delta_max=Decimal("0.14"),
        preferred_short_call_delta_min=Decimal("0.10"),
        preferred_short_call_delta_max=Decimal("0.12"),
        call_otm_min=Decimal("0.08"),
        call_otm_max=Decimal("0.14"),
        btc_call_delta_min=Decimal("0.08"),
        btc_call_delta_max=Decimal("0.12"),
        eth_call_delta_min=Decimal("0.06"),
        eth_call_delta_max=Decimal("0.10"),
        btc_call_otm_min=Decimal("0.10"),
        btc_call_otm_max=Decimal("0.18"),
        eth_call_otm_min=Decimal("0.12"),
        eth_call_otm_max=Decimal("0.20"),
        btc_preferred_call_delta_min=Decimal("0.09"),
        btc_preferred_call_delta_max=Decimal("0.11"),
        eth_preferred_call_delta_min=Decimal("0.07"),
        eth_preferred_call_delta_max=Decimal("0.09"),
        btc_preferred_call_otm_min=Decimal("0.12"),
        btc_preferred_call_otm_max=Decimal("0.16"),
        eth_preferred_call_otm_min=Decimal("0.14"),
        eth_preferred_call_otm_max=Decimal("0.18"),
        bull_put_long_delta_min=Decimal("0.02"),
        bull_put_long_delta_max=Decimal("0.05"),
        covered_call_spot_exit_enabled=False,
        covered_call_robust_exit_enabled=False,
        covered_call_robust_exit_dte=Decimal("0.5"),
        covered_call_itm_buffer_pct=Decimal("0"),
        covered_call_spot_order_type="market",
        covered_call_spot_max_slippage_pct=Decimal("0"),
        covered_call_profit_sweep_enabled=False,
        covered_call_slot_sizing=True,
    )
    values.update(overrides)
    if values.get("covered_call_profit_sweep_enabled"):
        traded = list(values.get("traded_collaterals") or ("BTC", "ETH", "USDC"))
        if "USDT" not in traded:
            values["traded_collaterals"] = tuple(traded + ["USDT"])
    return BotConfig(**values)


class FakeClient:
    def __init__(
        self,
        *,
        drawdowns=None,
        dvol_ratios=None,
        btc_book_equity: str | None = None,
        eth_book_equity: str | None = None,
        btc_initial_margin: str | None = None,
        eth_initial_margin: str | None = None,
        btc_maintenance_margin: str | None = None,
        eth_maintenance_margin: str | None = None,
    ):
        self.drawdowns = drawdowns or {"BTC": Decimal("-0.02"), "ETH": Decimal("-0.02")}
        self.dvol_ratios = dvol_ratios or {"BTC": Decimal("1.10"), "ETH": Decimal("1.10")}
        self.btc_book_equity = btc_book_equity
        self.eth_book_equity = eth_book_equity
        self.btc_initial_margin = btc_initial_margin
        self.eth_initial_margin = eth_initial_margin
        self.btc_maintenance_margin = btc_maintenance_margin
        self.eth_maintenance_margin = eth_maintenance_margin
        self.transaction_log: dict[str, list[dict]] = {}
        self.order_book_overrides: dict[str, dict] = {}
        self.placed_orders: list[dict] = []
        self.cancelled_orders: list[str] = []
        self.closed_positions: list[str] = []
        self.open_orders: list[dict] = []
        self.positions: list[dict] = []
        self.order_states: dict[str, dict] = {}
        self.user_trades_by_order: dict[str, list[dict]] = {}
        self.order_scripts_by_label: dict[str, list[dict]] = {}
        self.combos: dict[str, dict] = {}
        self._order_counter = 1
        self._combo_counter = 1

    def ping(self):
        return {"version": "test"}

    def get_instruments(self, currency, *, kind="option", expired=False):
        currency = currency.upper()
        if kind == "future":
            if currency == "BTC":
                return [
                    {
                        "instrument_name": "BTC-PERPETUAL",
                        "base_currency": "BTC",
                        "quote_currency": "USD",
                        "settlement_currency": "BTC",
                        "instrument_type": "reversed",
                        "kind": "future",
                        "tick_size": "0.5",
                        "min_trade_amount": "10",
                        "contract_size": "10",
                        "instrument_state": "open",
                    }
                ]
            if currency == "ETH":
                return [
                    {
                        "instrument_name": "ETH-PERPETUAL",
                        "base_currency": "ETH",
                        "quote_currency": "USD",
                        "settlement_currency": "ETH",
                        "instrument_type": "reversed",
                        "kind": "future",
                        "tick_size": "0.05",
                        "min_trade_amount": "1",
                        "contract_size": "1",
                        "instrument_state": "open",
                    }
                ]
            return []
        if kind == "spot":
            return [
                {
                    "instrument_name": "BTC_USDC",
                    "base_currency": "BTC",
                    "quote_currency": "USDC",
                    "settlement_currency": "USDC",
                    "instrument_type": "spot",
                    "kind": "spot",
                    "tick_size": "0.5",
                    "min_trade_amount": "0.0001",
                    "contract_size": "0.0001",
                    "instrument_state": "open",
                },
                {
                    "instrument_name": "ETH_USDC",
                    "base_currency": "ETH",
                    "quote_currency": "USDC",
                    "settlement_currency": "USDC",
                    "instrument_type": "spot",
                    "kind": "spot",
                    "tick_size": "0.05",
                    "min_trade_amount": "0.001",
                    "contract_size": "0.001",
                    "instrument_state": "open",
                },
                {
                    "instrument_name": "BTC_USDT",
                    "base_currency": "BTC",
                    "quote_currency": "USDT",
                    "settlement_currency": "USDT",
                    "instrument_type": "spot",
                    "kind": "spot",
                    "tick_size": "0.5",
                    "min_trade_amount": "0.0001",
                    "contract_size": "0.0001",
                    "instrument_state": "open",
                },
                {
                    "instrument_name": "ETH_USDT",
                    "base_currency": "ETH",
                    "quote_currency": "USDT",
                    "settlement_currency": "USDT",
                    "instrument_type": "spot",
                    "kind": "spot",
                    "tick_size": "0.05",
                    "min_trade_amount": "0.001",
                    "contract_size": "0.001",
                    "instrument_state": "open",
                },
            ]
        if kind != "option":
            return []
        if currency == "USDC":
            result = []
            for base_currency, _index_price, short_strike, long_strike, tick, min_amount in (
                ("BTC", Decimal("70000"), Decimal("63000"), Decimal("60000"), "2.5", "0.01"),
                ("ETH", Decimal("3500"), Decimal("3150"), Decimal("3000"), "0.5", "0.1"),
            ):
                for days in (14, 21):
                    expiry = future_expiry(days)
                    result.extend(
                        [
                            {
                                "instrument_name": f"{base_currency}_USDC-{days:02d}APR30-{short_strike}-P",
                                "base_currency": base_currency,
                                "quote_currency": "USDC",
                                "settlement_currency": "USDC",
                                "instrument_type": "linear",
                                "tick_size": tick,
                                "tick_size_steps": [],
                                "min_trade_amount": min_amount,
                                "contract_size": min_amount,
                                "option_type": "put",
                                "expiration_timestamp": expiry,
                                "strike": str(short_strike),
                                "instrument_state": "open",
                            },
                            {
                                "instrument_name": f"{base_currency}_USDC-{days:02d}APR30-{long_strike}-P",
                                "base_currency": base_currency,
                                "quote_currency": "USDC",
                                "settlement_currency": "USDC",
                                "instrument_type": "linear",
                                "tick_size": tick,
                                "tick_size_steps": [],
                                "min_trade_amount": min_amount,
                                "contract_size": min_amount,
                                "option_type": "put",
                                "expiration_timestamp": expiry,
                                "strike": str(long_strike),
                                "instrument_state": "open",
                            },
                        ]
                    )
            return result

        index_price = Decimal("70000") if currency == "BTC" else Decimal("3500")
        short_strike = Decimal("63000") if currency == "BTC" else Decimal("3150")
        long_strike = Decimal("62500") if currency == "BTC" else Decimal("3100")
        call_strike = Decimal("77000") if currency == "BTC" else Decimal("3850")
        tick = "0.0001"
        min_amount = "0.1" if currency == "BTC" else "1"
        result = []
        for days in (14, 21):
            expiry = future_expiry(days)
            result.extend(
                [
                    {
                        "instrument_name": f"{currency}-{days:02d}APR30-{short_strike}-P",
                        "base_currency": currency,
                        "quote_currency": currency,
                        "settlement_currency": currency,
                        "instrument_type": "reversed",
                        "tick_size": tick,
                        "tick_size_steps": [{"above_price": "0.005", "tick_size": "0.0005"}],
                        "min_trade_amount": min_amount,
                        "contract_size": min_amount,
                        "option_type": "put",
                        "expiration_timestamp": expiry,
                        "strike": str(short_strike),
                        "instrument_state": "open",
                    },
                    {
                        "instrument_name": f"{currency}-{days:02d}APR30-{long_strike}-P",
                        "base_currency": currency,
                        "quote_currency": currency,
                        "settlement_currency": currency,
                        "instrument_type": "reversed",
                        "tick_size": tick,
                        "tick_size_steps": [{"above_price": "0.005", "tick_size": "0.0005"}],
                        "min_trade_amount": min_amount,
                        "contract_size": min_amount,
                        "option_type": "put",
                        "expiration_timestamp": expiry,
                        "strike": str(long_strike),
                        "instrument_state": "open",
                    },
                    {
                        "instrument_name": f"{currency}-{days:02d}APR30-{call_strike}-C",
                        "base_currency": currency,
                        "quote_currency": currency,
                        "settlement_currency": currency,
                        "instrument_type": "reversed",
                        "tick_size": tick,
                        "tick_size_steps": [{"above_price": "0.005", "tick_size": "0.0005"}],
                        "min_trade_amount": min_amount,
                        "contract_size": min_amount,
                        "option_type": "call",
                        "expiration_timestamp": expiry,
                        "strike": str(call_strike),
                        "instrument_state": "open",
                    },
                ]
            )
        return result

    def get_instrument(self, instrument_name):
        for currency in ("USDC", "BTC", "ETH"):
            for item in self.get_instruments(currency, kind="option", expired=False):
                if item["instrument_name"] == instrument_name:
                    return item
        raise KeyError(instrument_name)

    def get_order_book(self, instrument_name, *, depth=1):
        if instrument_name in self.order_book_overrides:
            return self.order_book_overrides[instrument_name]
        if instrument_name.endswith("PERPETUAL"):
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": index_price,
                "best_bid_amount": "100",
                "best_ask_price": index_price,
                "best_ask_amount": "100",
                "mark_price": index_price,
                "index_price": index_price,
                "mark_iv": "0",
                "open_interest": "1000",
                "greeks": {"delta": "1"},
            }
        if "_USDC" in instrument_name and ("63000" in instrument_name or "3150" in instrument_name):
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "600" if instrument_name.startswith("BTC") else "45",
                "best_bid_amount": "0.05" if instrument_name.startswith("BTC") else "2",
                "best_ask_price": "620" if instrument_name.startswith("BTC") else "47",
                "best_ask_amount": "0.05" if instrument_name.startswith("BTC") else "2",
                "mark_price": "610" if instrument_name.startswith("BTC") else "46",
                "index_price": index_price,
                "mark_iv": "0.55",
                "open_interest": "60",
                "greeks": {"delta": "-0.11"},
            }
        if "_USDC" in instrument_name and ("60000" in instrument_name or "3000" in instrument_name):
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "190" if instrument_name.startswith("BTC") else "16",
                "best_bid_amount": "0.08" if instrument_name.startswith("BTC") else "2",
                "best_ask_price": "200" if instrument_name.startswith("BTC") else "17",
                "best_ask_amount": "0.08" if instrument_name.startswith("BTC") else "2",
                "mark_price": "195" if instrument_name.startswith("BTC") else "16.5",
                "index_price": index_price,
                "mark_iv": "0.52",
                "open_interest": "80",
                "greeks": {"delta": "-0.05"},
            }
        if "63000" in instrument_name or "3150" in instrument_name:
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "0.0032" if instrument_name.startswith("BTC") else "0.012",
                "best_bid_amount": "0.3" if instrument_name.startswith("BTC") else "4",
                "best_ask_price": "0.0034" if instrument_name.startswith("BTC") else "0.0125",
                "best_ask_amount": "0.3" if instrument_name.startswith("BTC") else "4",
                "mark_price": "0.0033" if instrument_name.startswith("BTC") else "0.0122",
                "index_price": index_price,
                "mark_iv": "0.55",
                "open_interest": "60",
                "greeks": {"delta": "-0.11"},
            }
        if "62500" in instrument_name or "3100" in instrument_name:
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "0.0013" if instrument_name.startswith("BTC") else "0.004",
                "best_bid_amount": "0.4" if instrument_name.startswith("BTC") else "5",
                "best_ask_price": "0.0015" if instrument_name.startswith("BTC") else "0.0045",
                "best_ask_amount": "0.4" if instrument_name.startswith("BTC") else "5",
                "mark_price": "0.0014" if instrument_name.startswith("BTC") else "0.0042",
                "index_price": index_price,
                "mark_iv": "0.52",
                "open_interest": "80",
                "greeks": {"delta": "-0.05"},
            }
        if instrument_name.endswith("-C"):
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "0.0032" if instrument_name.startswith("BTC") else "0.012",
                "best_bid_amount": "0.3" if instrument_name.startswith("BTC") else "4",
                "best_ask_price": "0.0034" if instrument_name.startswith("BTC") else "0.0125",
                "best_ask_amount": "0.3" if instrument_name.startswith("BTC") else "4",
                "mark_price": "0.0033" if instrument_name.startswith("BTC") else "0.0122",
                "index_price": index_price,
                "mark_iv": "0.55",
                "open_interest": "60",
                "greeks": {"delta": "0.11"},
            }
        if instrument_name in {"BTC_USDC", "ETH_USDC"}:
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": index_price,
                "best_bid_amount": "1",
                "best_ask_price": index_price,
                "best_ask_amount": "1",
                "mark_price": index_price,
                "index_price": index_price,
                "mark_iv": "0",
                "open_interest": "1000",
                "greeks": {"delta": "1"},
            }
        if instrument_name in {"BTC_USDT", "ETH_USDT"}:
            index_price = "70000" if instrument_name.startswith("BTC") else "3500"
            return {
                "instrument_name": instrument_name,
                "best_bid_price": index_price,
                "best_bid_amount": "1",
                "best_ask_price": index_price,
                "best_ask_amount": "1",
                "mark_price": index_price,
                "index_price": index_price,
                "mark_iv": "0",
                "open_interest": "1000",
                "greeks": {"delta": "1"},
            }
        raise KeyError(instrument_name)

    def get_index_price(self, index_name):
        if index_name.startswith("btc"):
            return {"index_price": Decimal("70000")}
        return {"index_price": Decimal("3500")}

    def get_index_chart_data(self, index_name, *, range_name="1d"):
        currency = "BTC" if index_name.startswith("btc") else "ETH"
        start = Decimal("100")
        end = start * (Decimal("1") + self.drawdowns[currency])
        return [[1, start], [2, end]]

    def get_volatility_index_data(self, currency, *, start_timestamp, end_timestamp, resolution="1D"):
        ratio = self.dvol_ratios[currency.upper()]
        return {
            "data": [[i, 1, 1, 1, 1] for i in range(1, 30)] + [[30, 1, 1, 1, ratio]],
            "continuation": None,
        }

    def get_funding_chart_data(self, instrument_name, *, length="8h"):
        return {"current_interest": 0, "interest_8h": 0, "data": []}

    def get_account_summaries(self, *, extended=False):
        btc_eq = self.btc_book_equity if self.btc_book_equity is not None else "0"
        eth_eq = self.eth_book_equity if self.eth_book_equity is not None else "0"
        btc_mm = (
            self.btc_maintenance_margin
            if self.btc_maintenance_margin is not None
            else ("0.01" if Decimal(btc_eq) > 0 else "0")
        )
        eth_mm = (
            self.eth_maintenance_margin
            if self.eth_maintenance_margin is not None
            else ("0.01" if Decimal(eth_eq) > 0 else "0")
        )
        btc_im = self.btc_initial_margin if self.btc_initial_margin is not None else "0"
        eth_im = self.eth_initial_margin if self.eth_initial_margin is not None else "0"
        return [
            {
                "currency": "USDC",
                "balance": "1000",
                "equity": "1000",
                "available_funds": "1000",
                "available_withdrawal_funds": "1000",
                "initial_margin": "20",
                "maintenance_margin": "5",
                "delta_total": "0",
                "options_delta": "0",
                "options_gamma": "0",
                "options_theta": "0",
            },
            {
                "currency": "BTC",
                "balance": btc_eq,
                "equity": btc_eq,
                "available_funds": btc_eq,
                "available_withdrawal_funds": btc_eq,
                "initial_margin": btc_im,
                "maintenance_margin": btc_mm,
                "delta_total": "0.04",
                "options_delta": "0",
                "options_gamma": "0",
                "options_theta": "0",
            },
            {
                "currency": "ETH",
                "balance": eth_eq,
                "equity": eth_eq,
                "available_funds": eth_eq,
                "available_withdrawal_funds": eth_eq,
                "initial_margin": eth_im,
                "maintenance_margin": eth_mm,
                "delta_total": "0",
                "options_delta": "0",
                "options_gamma": "0",
                "options_theta": "0",
            },
        ]

    def _filter_transaction_log_rows(
        self,
        *,
        currency: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        entries = getattr(self, "transaction_log", {})
        rows = entries.get(currency.upper(), []) if isinstance(entries, dict) else []
        return [
            row
            for row in rows
            if int(row.get("timestamp", 0)) >= int(start_timestamp)
            and int(row.get("timestamp", 0)) <= int(end_timestamp)
        ]

    def get_transaction_log(
        self,
        *,
        currency,
        start_timestamp,
        end_timestamp,
        count=100,
        subaccount_id=None,
        query=None,
        continuation=None,
    ):
        rows = self._filter_transaction_log_rows(
            currency=currency,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
        start = 0 if continuation in (None, "", 0) else int(continuation)
        return rows[start : start + int(count)]

    def iter_transaction_log(
        self,
        *,
        currency,
        start_timestamp,
        end_timestamp,
        count=100,
        subaccount_id=None,
        query=None,
    ):
        rows = self._filter_transaction_log_rows(
            currency=currency,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
        start = 0
        while start < len(rows):
            chunk = rows[start : start + int(count)]
            if not chunk:
                break
            yield from chunk
            start += len(chunk)

    def get_open_orders(self, *, kind="any"):
        return self.open_orders

    def get_open_orders_by_label(self, currency, label):
        return [order for order in self.open_orders if order.get("label") == label]

    def get_positions(self, *, currency="any", kind="any"):
        return self.positions

    def get_order_state(self, order_id):
        return self.order_states[order_id]

    def get_order_state_by_label(self, currency, label):
        for value in self.order_states.values():
            order = value.get("order") or {}
            if order.get("label") == label:
                return value
        return {}

    def get_user_trades_by_order(self, order_id, *, historical=False):
        return self.user_trades_by_order.get(order_id, [])

    def get_user_trades_by_currency(
        self,
        currency,
        *,
        kind=None,
        start_id=None,
        end_id=None,
        count=10,
        start_timestamp=None,
        end_timestamp=None,
        sorting=None,
        historical=False,
        subaccount_id=None,
    ):
        store = getattr(self, "user_trades_by_currency", None)
        if isinstance(store, dict):
            key = (str(currency).upper(), int(subaccount_id) if subaccount_id is not None else None)
            payload = store.get(key)
            if isinstance(payload, dict):
                return payload
        return {"trades": [], "has_more": False}

    def get_user_trades_by_instrument(
        self,
        instrument_name,
        *,
        start_seq=None,
        end_seq=None,
        count=10,
        start_timestamp=None,
        end_timestamp=None,
        sorting=None,
        historical=False,
        subaccount_id=None,
    ):
        store = getattr(self, "user_trades_by_instrument", None)
        if isinstance(store, dict):
            key = (str(instrument_name).strip(), int(subaccount_id) if subaccount_id is not None else None)
            payload = store.get(key)
            if isinstance(payload, dict):
                return payload
        return {"trades": [], "has_more": False}

    def create_combo(self, trades):
        combo_id = f"{trades[0]['instrument_name'].split('-', 1)[0]}-COMBO-{self._combo_counter}"
        self._combo_counter += 1
        combo = {
            "id": combo_id,
            "instrument_id": self._combo_counter,
            "creation_timestamp": 1,
            "state": "active",
            "legs": [
                {"instrument_name": trade["instrument_name"], "amount": ("1" if trade["direction"] == "buy" else "-1")}
                for trade in trades
            ],
            "trades": trades,
        }
        self.combos[combo_id] = combo
        return combo

    def _sync_option_positions_from_fill(
        self,
        *,
        instrument_name: str,
        direction: str,
        filled_amount: Decimal,
        reduce_only: bool,
    ) -> None:
        if filled_amount <= 0:
            return
        if instrument_name in self.combos or "PERPETUAL" in instrument_name:
            return
        if not (instrument_name.endswith("-P") or instrument_name.endswith("-C")):
            return

        def find_index() -> int | None:
            for i, p in enumerate(self.positions):
                if p.get("instrument_name") == instrument_name:
                    return i
            return None

        def new_option_row(dir_: str, size: Decimal) -> dict:
            s = str(size)
            return {
                "instrument_name": instrument_name,
                "direction": dir_,
                "kind": "option",
                "size": s,
                "size_currency": s,
                "mark_price": "0",
                "average_price": "0",
                "floating_profit_loss": "0",
                "delta": "0",
            }

        idx = find_index()
        d = direction.lower()
        ro = reduce_only

        if d == "buy" and ro:
            if idx is None:
                return
            p = self.positions[idx]
            if str(p.get("direction", "")).lower() != "sell":
                return
            cur = Decimal(str(p.get("size", "0")))
            new_sz = cur - filled_amount
            if new_sz <= 0:
                del self.positions[idx]
            else:
                p["size"] = str(new_sz)
            return

        if d == "buy" and not ro:
            if idx is None:
                self.positions.append(new_option_row("buy", filled_amount))
                return
            p = self.positions[idx]
            pd = str(p.get("direction", "")).lower()
            cur = Decimal(str(p.get("size", "0")))
            if pd == "buy":
                p["size"] = str(cur + filled_amount)
            elif pd == "sell":
                new_sz = cur - filled_amount
                if new_sz <= 0:
                    del self.positions[idx]
                else:
                    p["size"] = str(new_sz)
            return

        if d == "sell" and ro:
            if idx is None:
                return
            p = self.positions[idx]
            if str(p.get("direction", "")).lower() != "buy":
                return
            cur = Decimal(str(p.get("size", "0")))
            new_sz = cur - filled_amount
            if new_sz <= 0:
                del self.positions[idx]
            else:
                p["size"] = str(new_sz)
            return

        if d == "sell" and not ro:
            if idx is None:
                self.positions.append(new_option_row("sell", filled_amount))
                return
            p = self.positions[idx]
            pd = str(p.get("direction", "")).lower()
            cur = Decimal(str(p.get("size", "0")))
            if pd == "sell":
                p["size"] = str(cur + filled_amount)
            elif pd == "buy":
                new_sz = cur - filled_amount
                if new_sz <= 0:
                    del self.positions[idx]
                else:
                    p["size"] = str(new_sz)

    def place_order(self, *, direction, instrument_name, amount, label, order_type="limit", price=None, **kwargs):
        order_id = f"order-{self._order_counter}"
        self._order_counter += 1
        scripts = self.order_scripts_by_label.get(label) or []
        script = scripts.pop(0) if scripts else {}
        average_price = script.get("average_price", price or "0")
        order_state = script.get("order_state", "filled")
        filled_amount = script.get("filled_amount", amount)
        order = {
            "order_id": order_id,
            "instrument_name": instrument_name,
            "direction": direction,
            "order_state": order_state,
            "order_type": order_type,
            "amount": amount,
            "filled_amount": filled_amount,
            "price": price or "0",
            "average_price": average_price,
            "post_only": kwargs.get("post_only", False),
            "reduce_only": kwargs.get("reduce_only", False),
            "label": label,
            "creation_timestamp": 1,
        }
        trades = []
        if "trades" in script:
            trades = script["trades"]
        combo = self.combos.get(instrument_name)
        if combo is not None and not trades:
            amount_dec = Decimal(str(amount))
            price_dec = Decimal(str(price or "0"))
            first_leg = combo["trades"][0]["instrument_name"]
            index_price = Decimal("70000") if first_leg.startswith("BTC") else Decimal("3500")
            fee_currency = "USDC" if "_USDC-" in first_leg else ("BTC" if first_leg.startswith("BTC") else "ETH")
            fee = min(index_price * amount_dec * Decimal("0.0003"), price_dec * amount_dec * Decimal("0.125"))
            fee_value = fee if fee_currency == "USDC" else (fee / index_price if index_price > 0 else Decimal("0"))
            trades = [
                {
                    "order_id": order_id,
                    "instrument_name": instrument_name,
                    "price": price_dec,
                    "amount": amount_dec,
                    "fee": fee_value,
                    "fee_currency": fee_currency,
                    "index_price": index_price,
                    "timestamp": 1,
                }
            ]
        if (
            not trades
            and instrument_name not in self.combos
            and order_state == "filled"
            and Decimal(str(filled_amount or "0")) > 0
        ):
            amount_dec = Decimal(str(filled_amount))
            price_dec = Decimal(str(average_price or price or "0"))
            index_price = Decimal("70000") if instrument_name.startswith("BTC") else Decimal("3500")
            if price_dec <= 0 and ("_USDT" in instrument_name or "_USDC" in instrument_name):
                price_dec = index_price
            fee_currency = (
                "USDC" if "_USDC-" in instrument_name else ("BTC" if instrument_name.startswith("BTC") else "ETH")
            )
            fee = min(index_price * amount_dec * Decimal("0.0003"), price_dec * amount_dec * Decimal("0.125"))
            fee_value = fee if fee_currency == "USDC" else (fee / index_price if index_price > 0 else Decimal("0"))
            trades = [
                {
                    "order_id": order_id,
                    "instrument_name": instrument_name,
                    "direction": direction,
                    "price": price_dec,
                    "amount": amount_dec,
                    "fee": fee_value,
                    "fee_currency": fee_currency,
                    "index_price": index_price,
                    "timestamp": 1,
                }
            ]
        if trades:
            self.user_trades_by_order[order_id] = trades
        response = {"order": order, "trades": trades}
        self.placed_orders.append(
            {
                "direction": direction,
                "instrument_name": instrument_name,
                "amount": amount,
                "label": label,
                "order_type": order_type,
                "price": price,
                "post_only": kwargs.get("post_only", False),
                "reduce_only": kwargs.get("reduce_only", False),
                "time_in_force": kwargs.get("time_in_force"),
            }
        )
        self.order_states[order_id] = response
        fill_dec = Decimal(str(filled_amount or "0"))
        if fill_dec > 0:
            self._sync_option_positions_from_fill(
                instrument_name=instrument_name,
                direction=direction,
                filled_amount=fill_dec,
                reduce_only=bool(kwargs.get("reduce_only")),
            )
        return response

    def place_buy_order(self, **kwargs):
        return self.place_order(direction="buy", **kwargs)

    def place_sell_order(self, **kwargs):
        return self.place_order(direction="sell", **kwargs)

    def close_position(self, instrument_name, *, order_type="market", price=None):
        self.closed_positions.append(instrument_name)
        return {"instrument_name": instrument_name, "closed": True}

    def cancel_order(self, order_id):
        self.cancelled_orders.append(order_id)
        return {"order_id": order_id, "cancelled": True}

    def cancel_all(self, *, kind="any"):
        return {"cancelled": len(self.open_orders)}


@pytest.fixture
def fake_client():
    return FakeClient()


def make_book(
    *,
    name: str = "BTC_inverse",
    collateral: str = "BTC",
    inverse: bool = True,
    underlyings: tuple[str, ...] | None = None,
    equity: str = "1.0",
    initial_margin: str = "0",
    maintenance_margin: str = "0.01",
    per_leg_im_cap_put: str = "0.15",
    per_leg_im_cap_call: str = "0.12",
    expiry_im_cap: str = "0.30",
    book_im_target: str = "0.35",
    book_im_hard: str = "0.45",
    book_mm_target: str = "0.22",
    book_mm_hard: str = "0.33",
    min_open_interest: str = "20",
    max_spread_ratio: str = "0.12",
    min_book_notional_usdc: str = "3000",
) -> Book:
    """Construct a Book dataclass for tests with sane defaults.

    Overrides map 1:1 to ``Book`` fields. Keeps explicit defaults so tests can
    tweak only the axis under test without leaking other parameters.
    """
    if underlyings is None:
        underlyings = (collateral,)
    return Book(
        name=name,
        collateral=collateral,
        inverse=inverse,
        underlyings=underlyings,
        equity=Decimal(equity),
        initial_margin=Decimal(initial_margin),
        maintenance_margin=Decimal(maintenance_margin),
        per_leg_im_cap_put=Decimal(per_leg_im_cap_put),
        per_leg_im_cap_call=Decimal(per_leg_im_cap_call),
        expiry_im_cap=Decimal(expiry_im_cap),
        book_im_target=Decimal(book_im_target),
        book_im_hard=Decimal(book_im_hard),
        book_mm_target=Decimal(book_mm_target),
        book_mm_hard=Decimal(book_mm_hard),
        min_open_interest=Decimal(min_open_interest),
        max_spread_ratio=Decimal(max_spread_ratio),
        min_book_notional_usdc=Decimal(min_book_notional_usdc),
    )


@pytest.fixture
def btc_book():
    return make_book(name="BTC_inverse", collateral="BTC", underlyings=("BTC",))


@pytest.fixture
def eth_book():
    return make_book(name="ETH_inverse", collateral="ETH", underlyings=("ETH",), equity="10")


@pytest.fixture
def usdc_book():
    return make_book(
        name="USDC_linear",
        collateral="USDC",
        inverse=False,
        underlyings=("BTC", "ETH"),
        equity="5000",
        maintenance_margin="0",
        min_open_interest="8",
        max_spread_ratio="0.14",
        min_book_notional_usdc="4000",
    )
