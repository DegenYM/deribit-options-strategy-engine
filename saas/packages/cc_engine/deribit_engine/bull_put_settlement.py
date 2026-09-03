"""Bull put spread expiry settlement and reconcile PnL helpers."""

from __future__ import annotations

from decimal import Decimal

from .fees import option_trade_fee_usdc, premium_value_usdc
from .models import OptionInstrument, TradeGroup, normalize_strategy_name
from .stress import _intrinsic_settlement
from .utils import parse_option_name, safe_div, to_decimal, utc_now_ms

# Long leg may disappear from the positions API before the short at expiry.
SPREAD_EXPIRY_SETTLEMENT_BEFORE_MS = 3_600_000
SPREAD_EXPIRY_SETTLEMENT_AFTER_MS = 172_800_000
_MIN_PLAUSIBLE_UNDERLYING_INDEX_USD = Decimal("100")


def in_spread_expiry_settlement_window(
    expiration_timestamp_ms: int,
    *,
    now_ms: int | None = None,
) -> bool:
    exp = int(expiration_timestamp_ms or 0)
    if exp <= 0:
        return False
    now = utc_now_ms() if now_ms is None else now_ms
    return now >= exp - SPREAD_EXPIRY_SETTLEMENT_BEFORE_MS and now <= exp + SPREAD_EXPIRY_SETTLEMENT_AFTER_MS


def group_uses_spread_settlement_pricing(group: TradeGroup, *, now_ms: int | None = None) -> bool:
    """True when reconcile should use spread intrinsic settlement, not short mark."""
    exp = int(group.expiration_timestamp_ms or 0)
    if exp <= 0:
        return False
    now = utc_now_ms() if now_ms is None else now_ms
    if now >= exp - 60_000:
        return True
    return in_spread_expiry_settlement_window(exp, now_ms=now)


def is_bull_put_spread_group(group: TradeGroup, *, default_strategy: str = "naked_short") -> bool:
    strategy = normalize_strategy_name(group.strategy, default=default_strategy)
    return strategy == "bull_put_spread" or bool(group.long_instrument_name) or group.long_strike > 0


def long_instrument_for_spread_reconcile(
    group: TradeGroup,
    short_instrument: OptionInstrument,
    markets: dict[str, list[OptionInstrument]],
) -> OptionInstrument | None:
    if group.long_instrument_name:
        for instruments in markets.values():
            for inst in instruments:
                if inst.instrument_name == group.long_instrument_name:
                    return inst
        parsed = parse_option_name(group.long_instrument_name)
        if parsed:
            return OptionInstrument(
                instrument_name=group.long_instrument_name,
                base_currency=short_instrument.base_currency,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
                instrument_type=short_instrument.instrument_type,
                tick_size=short_instrument.tick_size,
                tick_size_steps=short_instrument.tick_size_steps,
                min_trade_amount=short_instrument.min_trade_amount,
                contract_size=short_instrument.contract_size,
                option_type="put",
                expiration_timestamp_ms=group.expiration_timestamp_ms or short_instrument.expiration_timestamp_ms,
                strike=to_decimal(parsed.get("strike")),
                instrument_state="closed",
            )
    if group.long_strike <= 0:
        return None
    return OptionInstrument(
        instrument_name=group.long_instrument_name or f"{short_instrument.instrument_name}-long-synth",
        base_currency=short_instrument.base_currency,
        quote_currency=short_instrument.quote_currency,
        settlement_currency=short_instrument.settlement_currency,
        instrument_type=short_instrument.instrument_type,
        tick_size=short_instrument.tick_size,
        tick_size_steps=short_instrument.tick_size_steps,
        min_trade_amount=short_instrument.min_trade_amount,
        contract_size=short_instrument.contract_size,
        option_type="put",
        expiration_timestamp_ms=group.expiration_timestamp_ms or short_instrument.expiration_timestamp_ms,
        strike=group.long_strike,
        instrument_state="closed",
    )


def cap_spread_reconcile_close_debit(group: TradeGroup, close_debit: Decimal) -> Decimal:
    if group.max_loss <= 0:
        return close_debit
    ceiling = group.entry_credit_net_usdc() + group.max_loss
    return min(close_debit, ceiling)


def spread_expiry_close_debit_usdc(
    group: TradeGroup,
    *,
    short_instrument: OptionInstrument,
    long_instrument: OptionInstrument | None,
    index_price: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
) -> Decimal:
    """Spread close debit at expiry (short intrinsic minus long intrinsic, fee-inclusive)."""
    option_type = (group.option_type or "put").lower()
    short_settle = _intrinsic_settlement(short_instrument, shocked_spot=index_price, option_type=option_type)
    short_debit = premium_value_usdc(
        index_price=index_price,
        premium=short_settle,
        quantity=group.quantity,
        base_currency=short_instrument.base_currency,
        quote_currency=short_instrument.quote_currency,
        settlement_currency=short_instrument.settlement_currency,
    )
    short_fee = option_trade_fee_usdc(
        index_price=index_price,
        premium=short_settle,
        quantity=group.quantity,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        base_currency=short_instrument.base_currency,
        quote_currency=short_instrument.quote_currency,
        settlement_currency=short_instrument.settlement_currency,
    )
    close_debit = short_debit + short_fee
    if long_instrument is not None and group.long_strike > 0:
        long_settle = _intrinsic_settlement(long_instrument, shocked_spot=index_price, option_type=option_type)
        long_credit = premium_value_usdc(
            index_price=index_price,
            premium=long_settle,
            quantity=group.quantity,
            base_currency=long_instrument.base_currency,
            quote_currency=long_instrument.quote_currency,
            settlement_currency=long_instrument.settlement_currency,
        )
        long_fee = option_trade_fee_usdc(
            index_price=index_price,
            premium=long_settle,
            quantity=group.quantity,
            fee_rate=fee_rate,
            fee_cap_rate=fee_cap_rate,
            base_currency=long_instrument.base_currency,
            quote_currency=long_instrument.quote_currency,
            settlement_currency=long_instrument.settlement_currency,
        )
        close_debit = max(close_debit - max(long_credit - long_fee, Decimal("0")), Decimal("0"))
    return cap_spread_reconcile_close_debit(group, close_debit)


def restore_long_leg_from_journal_executions(
    group: TradeGroup,
    executions: list[dict],
) -> bool:
    for row in executions:
        if str(row.get("leg") or "").lower() != "long":
            continue
        if str(row.get("event_type") or "").lower() != "open":
            continue
        name = str(row.get("instrument_name") or "")
        if not name:
            continue
        parsed = parse_option_name(name)
        if not parsed:
            continue
        group.long_instrument_name = name
        group.long_strike = to_decimal(parsed.get("strike"))
        if normalize_strategy_name(group.strategy) != "bull_put_spread":
            group.strategy = "bull_put_spread"
        return True
    return False


def spread_expiry_pnl_overstates_loss(group: TradeGroup) -> bool:
    """Heuristic: reconcile booked more loss than spread max_loss allows."""
    if group.status != "closed" or group.realized_pnl is None:
        return False
    if group.max_loss <= 0:
        return False
    reason = (group.close_reason or "").lower()
    if reason not in {"reconciled_expiry", "reconciled_external"}:
        return False
    floor = -group.max_loss - Decimal("2")
    return group.realized_pnl < floor


def settlement_index_usd_for_group(
    group: TradeGroup,
    *,
    spot_by_book: dict[str, Decimal] | None = None,
) -> Decimal | None:
    """Index at settlement for USDC linear options (underlying spot, not USDC book)."""
    underlying = (group.currency or "BTC").upper()
    for candidate in (group.close_index_usd, group.entry_index_usd):
        if candidate is not None and candidate >= _MIN_PLAUSIBLE_UNDERLYING_INDEX_USD:
            return candidate
    if spot_by_book:
        spot = spot_by_book.get(underlying)
        if spot is not None and spot >= _MIN_PLAUSIBLE_UNDERLYING_INDEX_USD:
            return spot
    return None


def should_repair_spread_expiry_reconcile_pnl(group: TradeGroup) -> bool:
    if group.status != "closed" or group.realized_pnl is None:
        return False
    if not is_bull_put_spread_group(group) or group.long_strike <= 0:
        return False
    reason = (group.close_reason or "").lower()
    return reason in {"reconciled_expiry", "reconciled_external"}


def repair_bull_put_expiry_reconcile_pnl(
    group: TradeGroup,
    *,
    index_price_usd: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    markets: dict[str, list[OptionInstrument]],
) -> bool:
    """Recompute closed spread PnL after mistaken naked-style expiry reconcile."""
    if not should_repair_spread_expiry_reconcile_pnl(group):
        return False
    if index_price_usd < _MIN_PLAUSIBLE_UNDERLYING_INDEX_USD:
        return False
    short_name = group.short_instrument_name
    short_inst: OptionInstrument | None = None
    for instruments in markets.values():
        for inst in instruments:
            if inst.instrument_name == short_name:
                short_inst = inst
                break
    if short_inst is None:
        parsed = parse_option_name(short_name)
        if not parsed:
            return False
        short_inst = OptionInstrument(
            instrument_name=short_name,
            base_currency=str(parsed.get("base_currency") or group.currency),
            quote_currency="USDC",
            settlement_currency="USDC",
            instrument_type="linear",
            tick_size=Decimal("0.0001"),
            tick_size_steps=(),
            min_trade_amount=Decimal("0.1"),
            contract_size=Decimal("0.1"),
            option_type="put",
            expiration_timestamp_ms=int(group.expiration_timestamp_ms or 0),
            strike=to_decimal(parsed.get("strike")),
            instrument_state="closed",
        )
    long_inst = long_instrument_for_spread_reconcile(group, short_inst, markets)
    close_debit = spread_expiry_close_debit_usdc(
        group,
        short_instrument=short_inst,
        long_instrument=long_inst,
        index_price=index_price_usd,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
    )
    new_pnl = group.entry_credit_net_usdc() - close_debit
    if group.realized_pnl is not None and abs(new_pnl - group.realized_pnl) <= Decimal("0.05"):
        return False
    group.realized_close_debit = close_debit
    group.realized_pnl = new_pnl
    group.realized_return_on_max_loss = safe_div(new_pnl, group.max_loss)
    group.backfill_realized_pnl_usdc(spot_index_usd=index_price_usd)
    if normalize_strategy_name(group.strategy) != "bull_put_spread":
        group.strategy = "bull_put_spread"
    return True
