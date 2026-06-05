from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .backtest_data import BacktestDataClient, pick_nearest_value
from .config import BotConfig
from .exit_eval import (
    backtest_remaining_apr_gate,
    backtest_tp_target_premium,
    exit_eval_context_from_config,
)
from .fee_discount import effective_option_fee_discount_rate
from .fees import option_trade_fee_native
from .margin import (
    linear_usdc_short_call_initial_per_contract_usdc,
    linear_usdc_short_call_mm_per_contract_usdc,
    linear_usdc_short_put_initial_per_contract_usdc,
    linear_usdc_short_put_mm_per_contract_usdc,
    short_call_initial_unit,
    short_call_maintenance_unit,
    short_put_initial_unit,
    short_put_maintenance_unit,
)
from .models import OptionInstrument, RiskRegime
from .strategy import StrategySelector
from .stress import StressScenario, stress_short_option_loss_breakdown_usdc, summarize_loss_entries
from .trade_apr import opened_notional_for_position
from .utils import ONE, ZERO, dte_days, safe_div, to_decimal
from .vol_metrics import (
    dvol_iv_rank_at_ts,
    iv_minus_rv_spread,
    passes_iv_entry_gate,
    realized_vol_annualized_from_index_series,
)

LOGGER = logging.getLogger(__name__)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def bs_delta(*, spot: float, strike: float, t_years: float, sigma: float, option_type: str) -> float:
    """Black-Scholes delta (no rates, no dividends).

    Used as an approximation when historical greeks are not available.
    """
    if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t_years) / (sigma * math.sqrt(t_years))
    call = _norm_cdf(d1)
    if option_type == "call":
        return call
    return call - 1.0


def bs_price(*, spot: float, strike: float, t_years: float, sigma: float, option_type: str) -> float:
    """Black-Scholes option price in quote currency (USD-like), r=0."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 0 or sigma <= 0:
        intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
        return intrinsic
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


@dataclass(frozen=True)
class BacktestConfig:
    start: datetime
    end: datetime
    resolution: str = "1D"
    cache_root: str = "data/backtest_cache"
    # Equity split across books (BTC/ETH/USDC) in USDC terms.
    book_weights: dict[str, Decimal] = None  # type: ignore[assignment]
    # Conservative spread proxy when reconstructing bid/ask from a single close.
    assumed_spread_ratio: Decimal = Decimal("0.06")

    def __post_init__(self):
        if self.book_weights is None:
            object.__setattr__(
                self, "book_weights", {"BTC": Decimal("0.3334"), "ETH": Decimal("0.3333"), "USDC": Decimal("0.3333")}
            )


@dataclass
class OpenLeg:
    instrument: OptionInstrument
    option_type: str
    quantity: Decimal
    entry_ts_ms: int
    entry_spot: Decimal
    entry_premium: Decimal
    entry_fee: Decimal
    collateral_currency: str
    estimated_im_collateral: Decimal = ZERO
    last_mark_premium: Decimal = ZERO

    def strike(self) -> Decimal:
        return self.instrument.strike


@dataclass
class BacktestResult:
    params: dict[str, Any]
    trades: list[dict[str, Any]]
    daily: list[dict[str, Any]]
    notes: list[str]
    stress: dict[str, Any] | None = None


def _day_range(start: datetime, end: datetime) -> list[datetime]:
    cur = datetime(start.year, start.month, start.day, tzinfo=UTC)
    last = datetime(end.year, end.month, end.day, tzinfo=UTC)
    out = []
    while cur <= last:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _guess_index_name(currency: str) -> str:
    c = currency.lower()
    if c in {"btc", "eth"}:
        return f"{c}_usd"
    return f"{c}_usd"


def _otm_ratio(*, spot: Decimal, strike: Decimal, option_type: str) -> Decimal:
    if spot <= 0:
        return ZERO
    if option_type == "call":
        return safe_div(strike, spot) - ONE
    return ONE - safe_div(strike, spot)


def _regime_from_drawdown_and_dvol(
    config: BotConfig,
    *,
    spot_return_24h: Decimal | None,
    dvol_ratio: Decimal | None,
) -> RiskRegime:
    # Mirror engine philosophy: crisis if index drawdown or DVOL ratio beyond threshold.
    if spot_return_24h is not None:
        if spot_return_24h <= -config.index_drawdown_crisis_pct:
            return RiskRegime.CRISIS
        if spot_return_24h <= -config.index_drawdown_elevated_pct:
            return RiskRegime.ELEVATED
    if dvol_ratio is not None:
        if dvol_ratio >= config.dvol_crisis_multiplier:
            return RiskRegime.CRISIS
        if dvol_ratio >= config.dvol_elevated_multiplier:
            return RiskRegime.ELEVATED
    return RiskRegime.NORMAL


def _estimate_sigma_from_dvol(dvol_close: Any | None) -> float:
    # DVOL endpoints typically return a decimal fraction (e.g. 0.65 = 65%).
    if dvol_close is None:
        return 0.80
    v = float(to_decimal(dvol_close))
    if v <= 0:
        return 0.80
    # Some feeds are already percent (65) not 0.65; clamp.
    if v > 3.0:
        v = v / 100.0
    return max(min(v, 3.0), 0.05)


def _unit_margins(
    instrument: OptionInstrument,
    *,
    index_price: Decimal,
    premium: Decimal,
    option_type: str,
) -> tuple[Decimal, Decimal]:
    """Return (im_per_contract, mm_per_contract) in the settlement currency unit."""
    inst_type = (instrument.instrument_type or "").lower()
    usdc = instrument.settlement_currency.upper() == "USDC" or inst_type == "linear"
    if usdc:
        if option_type == "call":
            return (
                linear_usdc_short_call_initial_per_contract_usdc(
                    index_price=index_price,
                    strike=instrument.strike,
                    mark_usdc=premium,
                    contract_size=instrument.contract_size,
                ),
                linear_usdc_short_call_mm_per_contract_usdc(
                    index_price=index_price,
                    strike=instrument.strike,
                    mark_usdc=premium,
                    contract_size=instrument.contract_size,
                ),
            )
        return (
            linear_usdc_short_put_initial_per_contract_usdc(
                index_price=index_price,
                strike=instrument.strike,
                mark_usdc=premium,
                contract_size=instrument.contract_size,
            ),
            linear_usdc_short_put_mm_per_contract_usdc(
                index_price=index_price,
                strike=instrument.strike,
                mark_usdc=premium,
                contract_size=instrument.contract_size,
            ),
        )
    # Inverse (coin-settled)
    if option_type == "call":
        return (
            short_call_initial_unit(index_price=index_price, strike=instrument.strike, mark_price=premium),
            short_call_maintenance_unit(index_price=index_price, strike=instrument.strike, mark_price=premium),
        )
    return (
        short_put_initial_unit(index_price=index_price, strike=instrument.strike, mark_price=premium),
        short_put_maintenance_unit(index_price=index_price, strike=instrument.strike, mark_price=premium),
    )


def _fee_for_trade(
    instrument: OptionInstrument,
    *,
    premium: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    index_price: Decimal,
    config: BotConfig,
    at_timestamp_ms: int,
    first_option_trade_timestamp_ms: int | None = None,
) -> Decimal:
    discount = effective_option_fee_discount_rate(
        base_rate=config.option_fee_discount_rate,
        discount_months=config.option_fee_discount_months,
        first_trade_timestamp_ms=first_option_trade_timestamp_ms,
        anchor=config.option_fee_discount_anchor,
        registration_timestamp_ms=config.option_fee_discount_registration_ms or None,
        at_timestamp_ms=at_timestamp_ms,
    )
    return option_trade_fee_native(
        index_price=index_price,
        premium=premium,
        quantity=quantity,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        quote_currency=instrument.quote_currency,
        settlement_currency=instrument.settlement_currency,
        fee_discount_rate=discount,
    )


def run_backtest(
    config: BotConfig,
    data: BacktestDataClient,
    bt: BacktestConfig,
    *,
    currencies: Iterable[str] = ("BTC", "ETH"),
) -> BacktestResult:
    notes: list[str] = []
    weights = {k.upper(): to_decimal(v) for k, v in (bt.book_weights or {}).items()}
    total_w = sum(weights.values(), ZERO)
    if total_w <= 0:
        weights = {"BTC": Decimal("0.3334"), "ETH": Decimal("0.3333"), "USDC": Decimal("0.3333")}
        total_w = sum(weights.values(), ZERO)
    weights = {k: safe_div(v, total_w) for k, v in weights.items()}

    days = _day_range(bt.start, bt.end)
    start_ms = _ms(days[0])
    end_ms = _ms(days[-1] + timedelta(days=1)) - 1

    # Load DVOL series for sigma proxy.
    dvol_by_ccy: dict[str, list[tuple[int, Any]]] = {}
    for c in currencies:
        dvol_by_ccy[c.upper()] = data.get_dvol_series(
            c.upper(), start_timestamp=start_ms, end_timestamp=end_ms, resolution="1D"
        )

    # Load index series (Deribit index chart is coarse for range; we still use it for 24h return).
    index_by_ccy: dict[str, list[tuple[int, Any]]] = {}
    for c in currencies:
        idx = _guess_index_name(c)
        # Use `all` to avoid missing older periods; cached anyway.
        index_by_ccy[c.upper()] = data.get_index_series(idx, range_name="all")

    instruments_by_ccy: dict[str, list[OptionInstrument]] = {}
    for c in currencies:
        instruments_by_ccy[c.upper()] = data.list_option_instruments(c.upper(), include_expired=True)
    # Also include USDC-linear options (BTC_USDC / ETH_USDC) under their underlyings.
    # This matches the bot's three-book design where the USDC book trades BTC/ETH linear options.
    try:
        usdc_instruments = data.list_option_instruments("USDC", include_expired=True)
        for inst in usdc_instruments:
            base = (inst.base_currency or "").upper()
            if base in instruments_by_ccy:
                instruments_by_ccy[base].append(inst)
    except Exception as exc:  # noqa: BLE001
        # USDC-linear options are optional coverage; record the gap in the report
        # notes so results are not silently incomplete.
        notes.append(f"usdc_linear_instruments_unavailable={type(exc).__name__}")
        LOGGER.warning("backtest: failed to load USDC linear instruments: %s", exc, exc_info=True)

    # Coverage note: Deribit public instruments listing may not include very old expired options.
    for c in currencies:
        if not instruments_by_ccy.get(c.upper()):
            notes.append(f"no_instruments_returned_for_currency={c.upper()} (public API coverage may be limited)")

    equity_usdc = config.reference_capital_usdc
    open_legs: list[OpenLeg] = []
    first_option_trade_ms: int | None = None
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    peak_equity = equity_usdc
    max_drawdown = Decimal("0")
    selector = StrategySelector(config)
    exit_ctx = exit_eval_context_from_config(config)

    # Staged black-swan ladder. Slippage increases with shock magnitude.
    scenarios = [
        StressScenario(name="spot_-10_slip_05%", spot_shock=Decimal("-0.10"), liquidity_slippage=Decimal("0.05")),
        StressScenario(name="spot_-20_slip_10%", spot_shock=Decimal("-0.20"), liquidity_slippage=Decimal("0.10")),
        StressScenario(name="spot_-30_slip_15%", spot_shock=Decimal("-0.30"), liquidity_slippage=Decimal("0.15")),
        StressScenario(name="spot_-40_slip_20%", spot_shock=Decimal("-0.40"), liquidity_slippage=Decimal("0.20")),
        StressScenario(name="spot_-50_slip_25%", spot_shock=Decimal("-0.50"), liquidity_slippage=Decimal("0.25")),
        StressScenario(name="spot_-60_slip_30%", spot_shock=Decimal("-0.60"), liquidity_slippage=Decimal("0.30")),
    ]
    stress_entries_by_scenario: dict[str, list[dict[str, Any]]] = {s.name: [] for s in scenarios}

    def _existing_im_by_expiry(*, collateral: str) -> dict[int, Decimal]:
        totals: dict[int, Decimal] = {}
        for leg in open_legs:
            if leg.collateral_currency.upper() != collateral.upper():
                continue
            exp = int(leg.instrument.expiration_timestamp_ms)
            totals[exp] = totals.get(exp, ZERO) + (
                leg.estimated_im_collateral if leg.estimated_im_collateral > 0 else ZERO
            )
        return totals

    for i, day in enumerate(days):
        day_ms = _ms(day)
        prev_ms = _ms(day - timedelta(days=1))

        day_row: dict[str, Any] = {
            "ts": day_ms,
            "date": day.strftime("%Y-%m-%d"),
            "opened": 0,
            "closed": 0,
            "open_count_start": len(open_legs),
            "equity_usdc_start": equity_usdc,
            "notes": [],
        }

        primary_ccy = currencies[0].upper()
        spot = to_decimal(pick_nearest_value(index_by_ccy[primary_ccy], ts_ms=day_ms) or 0)
        sigma = _estimate_sigma_from_dvol(pick_nearest_value(dvol_by_ccy[primary_ccy], ts_ms=day_ms))

        # Helper: theoretical premium at `day` (BS proxy).
        def mark_premium(inst: OptionInstrument) -> Decimal:
            dte = dte_days(inst.expiration_timestamp_ms, now=day)
            t_years = max(float(dte) / 365.0, 1e-6)
            px_usd = bs_price(
                spot=float(spot),
                strike=float(inst.strike),
                t_years=t_years,
                sigma=sigma,
                option_type=(inst.option_type or "").lower(),
            )
            if px_usd <= 0:
                return ZERO
            inst_type = (inst.instrument_type or "").lower()
            if (
                inst.settlement_currency.upper() == "USDC"
                or inst_type == "linear"
                or inst.quote_currency.upper() == "USDC"
            ):
                return to_decimal(px_usd) * (inst.contract_size if inst.contract_size > 0 else ONE)
            return safe_div(to_decimal(px_usd), spot, ZERO)

        # 1) Manage / close open legs.
        still_open: list[OpenLeg] = []
        for leg in open_legs:
            # If expired, close at intrinsic.
            if leg.instrument.expiration_timestamp_ms <= day_ms:
                spot = to_decimal(
                    pick_nearest_value(
                        index_by_ccy[leg.instrument.base_currency.upper()], ts_ms=leg.instrument.expiration_timestamp_ms
                    )
                    or 0
                )
                if spot <= 0:
                    spot = leg.entry_spot
                intrinsic = (
                    max(leg.strike() - spot, ZERO) if leg.option_type == "put" else max(spot - leg.strike(), ZERO)
                )
                # Settlement: linear is USDC, inverse is coin. Convert to USDC using spot if needed.
                pnl_settle = leg.entry_premium * leg.quantity - intrinsic * leg.quantity
                if leg.instrument.settlement_currency.upper() != "USDC":
                    pnl_usdc = pnl_settle * spot
                else:
                    pnl_usdc = pnl_settle
                equity_usdc += pnl_usdc
                trades.append(
                    {
                        "action": "close_expiry",
                        "instrument": leg.instrument.instrument_name,
                        "date": day.strftime("%Y-%m-%d"),
                        "spot": spot,
                        "pnl_usdc": pnl_usdc,
                    }
                )
                day_row["closed"] += 1
                continue

            # Mark premium for TP / MTM.
            leg_spot = to_decimal(
                pick_nearest_value(index_by_ccy[leg.instrument.base_currency.upper()], ts_ms=day_ms) or 0
            )
            if leg_spot <= 0:
                leg_spot = leg.entry_spot
            # Use per-underlying sigma proxy
            leg_dvol_close = pick_nearest_value(dvol_by_ccy[leg.instrument.base_currency.upper()], ts_ms=day_ms)
            leg_sigma = _estimate_sigma_from_dvol(leg_dvol_close)
            # Temporarily override outer `spot/sigma` for premium calc.
            spot_saved, sigma_saved = spot, sigma
            spot, sigma = leg_spot, leg_sigma
            current_premium = mark_premium(leg.instrument)
            spot, sigma = spot_saved, sigma_saved

            leg_dte = dte_days(leg.instrument.expiration_timestamp_ms, now=day)
            abs_delta = abs(
                to_decimal(
                    bs_delta(
                        spot=float(leg_spot),
                        strike=float(leg.strike()),
                        t_years=max(float(leg_dte) / 365.0, 1e-6),
                        sigma=float(leg_sigma),
                        option_type=leg.option_type,
                    )
                )
            )
            max_loss = (
                leg.estimated_im_collateral if leg.estimated_im_collateral > 0 else leg.entry_premium * leg.quantity
            )
            loss_amount = max((current_premium - leg.entry_premium) * leg.quantity, ZERO)
            loss_pct = safe_div(loss_amount, max_loss)
            hard_stop = abs_delta >= config.hard_defense_delta or loss_pct >= config.hard_stop_loss_pct
            if hard_stop:
                close_fee = _fee_for_trade(
                    leg.instrument,
                    premium=current_premium,
                    quantity=leg.quantity,
                    fee_rate=config.option_fee_rate,
                    fee_cap_rate=config.option_fee_cap_rate,
                    index_price=leg_spot,
                    config=config,
                    at_timestamp_ms=day_ms,
                    first_option_trade_timestamp_ms=first_option_trade_ms,
                )
                pnl_settle = (leg.entry_premium - current_premium) * leg.quantity - leg.entry_fee - close_fee
                pnl_usdc = pnl_settle if leg.instrument.settlement_currency.upper() == "USDC" else pnl_settle * leg_spot
                equity_usdc += pnl_usdc
                trades.append(
                    {
                        "action": "close_hard_stop",
                        "instrument": leg.instrument.instrument_name,
                        "date": day.strftime("%Y-%m-%d"),
                        "close_premium": current_premium,
                        "pnl_usdc": pnl_usdc,
                    }
                )
                day_row["closed"] += 1
                continue

            tp_target = backtest_tp_target_premium(leg.entry_premium, leg_dte, exit_ctx)
            if current_premium > 0 and current_premium <= tp_target:
                # Close at current_premium (proxy), include fee.
                close_fee = _fee_for_trade(
                    leg.instrument,
                    premium=current_premium,
                    quantity=leg.quantity,
                    fee_rate=config.option_fee_rate,
                    fee_cap_rate=config.option_fee_cap_rate,
                    index_price=leg_spot,
                    config=config,
                    at_timestamp_ms=day_ms,
                    first_option_trade_timestamp_ms=first_option_trade_ms,
                )
                pnl_settle = (leg.entry_premium - current_premium) * leg.quantity - leg.entry_fee - close_fee
                pnl_usdc = pnl_settle if leg.instrument.settlement_currency.upper() == "USDC" else pnl_settle * leg_spot
                equity_usdc += pnl_usdc
                trades.append(
                    {
                        "action": "close_tp",
                        "instrument": leg.instrument.instrument_name,
                        "date": day.strftime("%Y-%m-%d"),
                        "close_premium": current_premium,
                        "pnl_usdc": pnl_usdc,
                    }
                )
                day_row["closed"] += 1
                continue

            capital_base = opened_notional_for_position(
                strategy="naked_short",
                collateral_currency=leg.collateral_currency,
                option_type=leg.option_type,
                quantity=leg.quantity,
                contract_size=leg.instrument.contract_size,
                strike=leg.strike(),
                index_price_usd=leg_spot,
                estimated_im_collateral=leg.estimated_im_collateral,
                covered_underlying_quantity=ZERO,
            )
            close_fee_per = _fee_for_trade(
                leg.instrument,
                premium=current_premium,
                quantity=Decimal("1"),
                fee_rate=config.option_fee_rate,
                fee_cap_rate=config.option_fee_cap_rate,
                index_price=leg_spot,
                config=config,
                at_timestamp_ms=day_ms,
                first_option_trade_timestamp_ms=first_option_trade_ms,
            )
            if backtest_remaining_apr_gate(
                entry_premium=leg.entry_premium,
                current_premium=current_premium,
                close_fee_per_contract=close_fee_per,
                quantity=leg.quantity,
                capital_base=capital_base,
                dte_days=leg_dte,
                ctx=exit_ctx,
            ):
                close_fee = close_fee_per * leg.quantity
                pnl_settle = (leg.entry_premium - current_premium) * leg.quantity - leg.entry_fee - close_fee
                pnl_usdc = pnl_settle if leg.instrument.settlement_currency.upper() == "USDC" else pnl_settle * leg_spot
                equity_usdc += pnl_usdc
                trades.append(
                    {
                        "action": "close_early_exit",
                        "instrument": leg.instrument.instrument_name,
                        "date": day.strftime("%Y-%m-%d"),
                        "close_premium": current_premium,
                        "pnl_usdc": pnl_usdc,
                    }
                )
                day_row["closed"] += 1
                continue

            # Time exit: if DTE <= config.time_exit_dte, close at premium close (approx by TradingView close).
            dte = leg_dte
            if dte <= Decimal(str(config.time_exit_dte)):
                close = current_premium if current_premium > 0 else leg.entry_premium
                close_fee = _fee_for_trade(
                    leg.instrument,
                    premium=close,
                    quantity=leg.quantity,
                    fee_rate=config.option_fee_rate,
                    fee_cap_rate=config.option_fee_cap_rate,
                    index_price=leg_spot,
                    config=config,
                    at_timestamp_ms=day_ms,
                    first_option_trade_timestamp_ms=first_option_trade_ms,
                )
                pnl_settle = (leg.entry_premium - close) * leg.quantity - leg.entry_fee - close_fee
                pnl_usdc = pnl_settle if leg.instrument.settlement_currency.upper() == "USDC" else pnl_settle * leg_spot
                equity_usdc += pnl_usdc
                trades.append(
                    {
                        "action": "close_time_exit",
                        "instrument": leg.instrument.instrument_name,
                        "date": day.strftime("%Y-%m-%d"),
                        "close_premium": close,
                        "pnl_usdc": pnl_usdc,
                    }
                )
                day_row["closed"] += 1
                continue

            leg.last_mark_premium = current_premium
            still_open.append(leg)
        open_legs = still_open

        # 2) Determine regime per underlying (approx: 24h return from index + DVOL ratio vs 30d median)
        regime_by_ccy: dict[str, RiskRegime] = {}
        for c in currencies:
            series = index_by_ccy[c.upper()]
            spot = to_decimal(pick_nearest_value(series, ts_ms=day_ms) or 0)
            prev = to_decimal(pick_nearest_value(series, ts_ms=prev_ms) or 0)
            ret = None
            if spot > 0 and prev > 0:
                ret = (spot / prev) - ONE
            dvol_series = dvol_by_ccy[c.upper()]
            dv_today = to_decimal(pick_nearest_value(dvol_series, ts_ms=day_ms) or 0)
            # crude baseline: use last 30 points median-ish by selecting 15th after sorting
            baseline = None
            if len(dvol_series) >= 10:
                last = [to_decimal(v) for _t, v in dvol_series[max(0, i - 30) : i + 1] if v is not None]
                last_sorted = sorted([x for x in last if x > 0])
                if last_sorted:
                    baseline = last_sorted[len(last_sorted) // 2]
            dvol_ratio = None
            if baseline is not None and baseline > 0 and dv_today > 0:
                dvol_ratio = dv_today / baseline
            regime_by_ccy[c.upper()] = _regime_from_drawdown_and_dvol(
                config, spot_return_24h=ret, dvol_ratio=dvol_ratio
            )

        # 3) Entry: one new leg per day max, obey crisis halt + book weights + IM cap approximation.
        if len(open_legs) >= config.max_concurrent_groups:
            day_row["notes"].append("max_concurrent_groups reached")
            daily.append(day_row)
            continue

        # Pick best candidate across currencies and collateral books (simplified).
        iv_rank_by_currency: dict[str, Decimal] = {}
        iv_minus_rv_by_currency: dict[str, Decimal] = {}
        for c in currencies:
            ccy = c.upper()
            rank = dvol_iv_rank_at_ts(
                dvol_by_ccy[ccy],
                ts_ms=day_ms,
                lookback_days=config.iv_rank_lookback_days,
            )
            if rank is not None:
                iv_rank_by_currency[ccy] = rank
            dvol_close = pick_nearest_value(dvol_by_ccy[ccy], ts_ms=day_ms)
            current_iv = to_decimal(dvol_close or 0) / Decimal("100")
            rv = realized_vol_annualized_from_index_series(
                index_by_ccy[ccy],
                end_ts_ms=day_ms,
                window=config.rv_lookback_days,
            )
            if current_iv > 0 and rv is not None:
                spread = iv_minus_rv_spread(iv=current_iv, rv=rv)
                if spread is not None:
                    iv_minus_rv_by_currency[ccy] = spread
        selector.update_vol_entry_context(
            iv_rank_by_currency=iv_rank_by_currency,
            iv_minus_rv_by_currency=iv_minus_rv_by_currency,
        )

        best: tuple[Decimal, dict[str, Any]] | None = None
        for c in currencies:
            ccy = c.upper()
            if regime_by_ccy[ccy] is RiskRegime.CRISIS:
                continue
            min_iv_rank, max_iv_rank = config.iv_rank_bounds(ccy)
            if not passes_iv_entry_gate(
                iv_rank_value=iv_rank_by_currency.get(ccy),
                iv_minus_rv=iv_minus_rv_by_currency.get(ccy),
                min_iv_rank=min_iv_rank,
                max_iv_rank=max_iv_rank,
                min_iv_minus_rv=config.min_iv_minus_rv,
                gate_enabled=config.enable_iv_entry_gate,
            ):
                continue
            spot = to_decimal(pick_nearest_value(index_by_ccy[ccy], ts_ms=day_ms) or 0)
            if spot <= 0:
                continue
            dvol_close = pick_nearest_value(dvol_by_ccy[ccy], ts_ms=day_ms)
            sigma = _estimate_sigma_from_dvol(dvol_close)

            def _premium_for(inst: OptionInstrument) -> Decimal:
                return mark_premium(inst)

            # Build candidates using StrategySelector sizing logic with a synthetic orderbook snapshot.
            # We fabricate OI and book depth so liquidity gates can pass; spread_ratio is controlled via assumed_spread_ratio.
            from .models import OrderBookSnapshot  # local import to avoid circulars

            assumed = bt.assumed_spread_ratio
            premium_by_name: dict[str, Decimal] = {}
            delta_by_name: dict[str, Decimal] = {}

            def loader(name: str) -> OrderBookSnapshot:
                prem = premium_by_name.get(name, ZERO)
                if prem <= 0:
                    prem = Decimal("0")
                spr = assumed
                bid = prem * (ONE - spr / Decimal("2"))
                ask = prem * (ONE + spr / Decimal("2"))
                if ask <= 0:
                    ask = prem
                if bid < 0:
                    bid = ZERO
                return OrderBookSnapshot(
                    instrument_name=name,
                    best_bid_price=bid,
                    best_bid_amount=Decimal("1000000"),
                    best_ask_price=ask,
                    best_ask_amount=Decimal("1000000"),
                    mark_price=prem,
                    index_price=spot,
                    delta=delta_by_name.get(name, ZERO),
                    iv=Decimal(str(sigma)),
                    open_interest=Decimal("1000000"),
                )

            # Group instruments by collateral book for sizing caps.
            by_collateral: dict[str, list[OptionInstrument]] = {}
            for inst in instruments_by_ccy[ccy]:
                if inst.expiration_timestamp_ms <= day_ms:
                    continue
                if (inst.instrument_state or "").lower() not in {"open", "active", ""}:
                    continue
                dte = dte_days(inst.expiration_timestamp_ms, now=day)
                if dte < Decimal(str(config.entry_dte_min)) or dte > Decimal(str(config.entry_dte_max)):
                    continue
                side = (inst.option_type or "").lower()
                if side not in {"put", "call"}:
                    continue
                if side == "call" and not config.enable_short_call:
                    continue
                if side == "put" and not config.enable_short_put:
                    continue
                prem = _premium_for(inst)
                if prem <= 0:
                    continue
                otm = _otm_ratio(spot=spot, strike=inst.strike, option_type=side)
                omin, omax = config.otm_bounds(ccy, side)
                if not (omin <= otm <= omax):
                    continue
                t_years = max(float(dte) / 365.0, 1e-6)
                delta_f = bs_delta(
                    spot=float(spot),
                    strike=float(inst.strike),
                    t_years=t_years,
                    sigma=sigma,
                    option_type=side,
                )
                abs_delta = abs(delta_f)
                dmin, dmax = config.delta_bounds(ccy, side)
                if not (float(dmin) <= abs_delta <= float(dmax)):
                    continue
                premium_by_name[inst.instrument_name] = prem
                delta_by_name[inst.instrument_name] = Decimal(str(delta_f))
                collateral = (
                    inst.settlement_currency.upper()
                    if inst.settlement_currency
                    else (inst.quote_currency.upper() or "USDC")
                )
                by_collateral.setdefault(collateral, []).append(inst)

            for collateral, insts in sorted(by_collateral.items()):
                book_w = weights.get(collateral, ZERO)
                if book_w <= 0:
                    continue
                # Approx book equity in collateral units (coin books are equity_usdc converted by spot).
                book_equity = (equity_usdc * book_w) if collateral == "USDC" else (equity_usdc * book_w) / spot
                if book_equity <= 0:
                    continue
                existing_im = _existing_im_by_expiry(collateral=collateral)
                # Use put-first fallback by default; when fallback_only is false,
                # puts and calls compete in the same candidate pool.
                regime = regime_by_ccy.get(ccy, RiskRegime.CRISIS)
                if regime is RiskRegime.CRISIS:
                    continue
                candidates = []
                if config.option_strategy == "bull_put_spread":
                    candidates = selector.build_bull_put_spread_candidates(
                        insts,
                        loader,
                        regime=regime,
                        summary_equity=book_equity,
                        summary_maintenance_margin=ZERO,
                        collateral_currency=collateral,
                        currency=ccy,
                        existing_im_by_expiry=existing_im,
                    )
                elif config.enable_short_put:
                    candidates = selector.build_naked_short_put_candidates(
                        insts,
                        loader,
                        regime=regime,
                        summary_equity=book_equity,
                        summary_maintenance_margin=ZERO,
                        collateral_currency=collateral,
                        currency=ccy,
                        existing_im_by_expiry=existing_im,
                    )
                if config.enable_short_call and (not config.short_call_fallback_only or not candidates):
                    candidates.extend(
                        selector.build_naked_short_call_candidates(
                            insts,
                            loader,
                            regime=regime,
                            summary_equity=book_equity,
                            summary_maintenance_margin=ZERO,
                            collateral_currency=collateral,
                            currency=ccy,
                            existing_im_by_expiry=existing_im,
                        )
                    )
                if not candidates:
                    continue
                # Rank with side-aware preferred delta/OTM bands.
                top = sorted(
                    candidates,
                    key=selector.naked_put_sort_key,
                )[0]
                score = top.margin_efficiency
                # Entry premium from the synthetic book (best_bid).
                entry_prem = premium_by_name.get(top.short_leg.instrument_name, ZERO)
                payload = {
                    "currency": ccy,
                    "instrument": top.short_leg.instrument_name,
                    "option_type": top.short_leg.instrument_name.split("-")[-1]
                    .lower()
                    .replace("p", "put")
                    .replace("c", "call"),
                    "spot": spot,
                    "premium": entry_prem,
                    "qty": top.quantity,
                    "collateral": collateral,
                    "delta_est": top.short_leg.delta,
                    "otm": _otm_ratio(
                        spot=spot,
                        strike=top.short_leg.strike,
                        option_type="put" if top.short_leg.instrument_name.upper().endswith("-P") else "call",
                    ),
                    "im_usdc": (top.estimated_im_total if collateral == "USDC" else top.estimated_im_total * spot),
                    "score": score,
                    "estimated_im_collateral": top.estimated_im_total,
                }
                if best is None or score > best[0]:
                    best = (score, payload)

        if best is not None:
            chosen = best[1]
            inst_name = str(chosen["instrument"])
            # Locate instrument object
            base = str(chosen["currency"])
            inst_obj = next((x for x in instruments_by_ccy[base] if x.instrument_name == inst_name), None)
            if inst_obj is None:
                day_row["notes"].append("chosen instrument not found in instrument list")
                daily.append(day_row)
                continue
            leg = OpenLeg(
                instrument=inst_obj,
                option_type=str(chosen["option_type"]),
                quantity=to_decimal(chosen["qty"]),
                entry_ts_ms=day_ms,
                entry_spot=to_decimal(chosen["spot"]),
                entry_premium=to_decimal(chosen["premium"]),
                entry_fee=_fee_for_trade(
                    inst_obj,
                    premium=to_decimal(chosen["premium"]),
                    quantity=to_decimal(chosen["qty"]),
                    fee_rate=config.option_fee_rate,
                    fee_cap_rate=config.option_fee_cap_rate,
                    index_price=to_decimal(chosen["spot"]),
                    config=config,
                    at_timestamp_ms=day_ms,
                    first_option_trade_timestamp_ms=first_option_trade_ms,
                ),
                collateral_currency=str(chosen["collateral"]),
                estimated_im_collateral=to_decimal(chosen.get("estimated_im_collateral") or 0),
            )
            open_legs.append(leg)
            if first_option_trade_ms is None:
                first_option_trade_ms = day_ms
            trades.append({"action": "open", "date": day.strftime("%Y-%m-%d"), **chosen})
            day_row["opened"] += 1

        # 4) Black swan overlay (portfolio-level, book-capped).
        #
        # Rationale:
        # - Naked short options can have losses far exceeding initial margin.
        # - In a real segregated Standard Margin setup, liquidation occurs before the
        #   portfolio can lose more than each book's equity (BTC/ETH/USDC pools are isolated).
        # Here we cap per-book losses by that book's USDC-equivalent equity.
        book_equity_usdc: dict[str, Decimal] = {
            book: (equity_usdc * w if w > 0 else ZERO) for book, w in weights.items()
        }
        for s in scenarios:
            loss_by_book: dict[str, Decimal] = {}
            base_by_book: dict[str, Decimal] = {}
            slip_by_book: dict[str, Decimal] = {}
            for leg in open_legs:
                spot = to_decimal(
                    pick_nearest_value(index_by_ccy[leg.instrument.base_currency.upper()], ts_ms=day_ms) or 0
                )
                if spot <= 0:
                    continue
                bd = stress_short_option_loss_breakdown_usdc(
                    leg.instrument,
                    option_type=leg.option_type,
                    quantity=leg.quantity,
                    entry_premium=leg.entry_premium,
                    spot=spot,
                    scenario=s,
                )
                loss = to_decimal(bd["total_usdc"])
                b = (leg.collateral_currency or "").upper() or "USDC"
                loss_by_book[b] = loss_by_book.get(b, ZERO) + loss
                base_by_book[b] = base_by_book.get(b, ZERO) + to_decimal(bd["base_move_usdc"])
                slip_by_book[b] = slip_by_book.get(b, ZERO) + to_decimal(bd["slippage_usdc"])
            capped_total = ZERO
            capped_by_book: dict[str, Decimal] = {}
            capped_components: dict[str, dict[str, Decimal]] = {}
            for b, loss in loss_by_book.items():
                eq = book_equity_usdc.get(b, ZERO)
                # Loss is negative for losing scenarios; cap at wiping out the book.
                capped = max(loss, -eq) if eq > 0 else loss
                capped_by_book[b] = capped
                capped_total += capped
                # Component attribution (also capped proportionally by the same cap).
                base = base_by_book.get(b, ZERO)
                slip = slip_by_book.get(b, ZERO)
                raw = base + slip
                if raw == 0:
                    capped_components[b] = {"base_move_usdc": base, "slippage_usdc": slip}
                else:
                    ratio = safe_div(capped, raw, ONE)
                    capped_components[b] = {"base_move_usdc": base * ratio, "slippage_usdc": slip * ratio}
            stress_entries_by_scenario[s.name].append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "loss": capped_total,
                    "by_book": {k: str(v) for k, v in capped_by_book.items()},
                    "components": {
                        "by_book": {
                            bk: {ck: str(cv) for ck, cv in comps.items()} for bk, comps in capped_components.items()
                        },
                        "total": {
                            "base_move_usdc": str(sum((c["base_move_usdc"] for c in capped_components.values()), ZERO)),
                            "slippage_usdc": str(sum((c["slippage_usdc"] for c in capped_components.values()), ZERO)),
                        },
                    },
                }
            )

        day_row["open_count_end"] = len(open_legs)
        day_row["equity_usdc_end"] = equity_usdc
        if equity_usdc > peak_equity:
            peak_equity = equity_usdc
        dd = safe_div(max(peak_equity - equity_usdc, ZERO), peak_equity, ZERO)
        if dd > max_drawdown:
            max_drawdown = dd
        day_row["drawdown_pct"] = dd
        daily.append(day_row)

    stress_summary = {name: summarize_loss_entries(values) for name, values in stress_entries_by_scenario.items()}
    # Force-close remaining open legs at end (mark-to-market close).
    if open_legs:
        final_day = days[-1]
        final_ms = _ms(final_day)
        for leg in list(open_legs):
            leg_spot = to_decimal(
                pick_nearest_value(index_by_ccy[leg.instrument.base_currency.upper()], ts_ms=final_ms) or leg.entry_spot
            )
            leg_dvol_close = pick_nearest_value(dvol_by_ccy[leg.instrument.base_currency.upper()], ts_ms=final_ms)
            leg_sigma = _estimate_sigma_from_dvol(leg_dvol_close)
            spot_saved, sigma_saved = spot, sigma
            spot, sigma = leg_spot, leg_sigma
            close = mark_premium(leg.instrument)
            spot, sigma = spot_saved, sigma_saved
            close_fee = _fee_for_trade(
                leg.instrument,
                premium=close,
                quantity=leg.quantity,
                fee_rate=config.option_fee_rate,
                fee_cap_rate=config.option_fee_cap_rate,
                index_price=leg_spot,
                config=config,
                at_timestamp_ms=final_ms,
                first_option_trade_timestamp_ms=first_option_trade_ms,
            )
            pnl_settle = (leg.entry_premium - close) * leg.quantity - leg.entry_fee - close_fee
            pnl_usdc = pnl_settle if leg.instrument.settlement_currency.upper() == "USDC" else pnl_settle * leg_spot
            equity_usdc += pnl_usdc
            trades.append(
                {
                    "action": "close_end",
                    "instrument": leg.instrument.instrument_name,
                    "date": final_day.strftime("%Y-%m-%d"),
                    "close_premium": close,
                    "pnl_usdc": pnl_usdc,
                }
            )
            open_legs.remove(leg)

    open_count = len([t for t in trades if t.get("action") == "open"])
    return BacktestResult(
        params={
            "start": bt.start.isoformat(),
            "end": bt.end.isoformat(),
            "resolution": bt.resolution,
            "option_strategy": config.option_strategy,
            "reference_capital_usdc": config.reference_capital_usdc,
            "book_weights": {k: str(v) for k, v in weights.items()},
            "final_equity_usdc": equity_usdc,
            "total_pnl_usdc": equity_usdc - config.reference_capital_usdc,
            "max_drawdown_pct": max_drawdown,
            "open_trade_count": open_count,
        },
        trades=trades,
        daily=daily,
        notes=notes,
        stress=stress_summary,
    )
