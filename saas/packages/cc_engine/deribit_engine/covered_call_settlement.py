"""Covered-call expiry settlement loss for spot-exit sizing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import OptionInstrument, TradeGroup
from .stress import _intrinsic_settlement
from .utils import to_decimal

SETTLEMENT_LOG_TYPES: frozenset[str] = frozenset({"settlement", "delivery"})
DEFAULT_SETTLEMENT_LOG_WINDOW_MS = 3_600_000


def covered_call_spot_exit_skips_settlement_loss(*, reason: str = "", spot_exit_reason: str = "") -> bool:
    """Robust exits buy back the call before expiry — no coin settlement debit."""
    probe = f"{reason} {spot_exit_reason}".lower()
    return "robust" in probe


def covered_call_settlement_loss_from_intrinsic(
    group: TradeGroup,
    *,
    index_price_usd: Decimal,
    short_instrument: OptionInstrument | None = None,
) -> Decimal:
    """Estimate ITM short-call settlement debit in native coin (inverse-style)."""
    if index_price_usd <= 0 or group.quantity <= 0:
        return Decimal("0")
    if (group.option_type or "").lower() != "call":
        return Decimal("0")
    strike = (
        group.short_strike
        if group.short_strike > 0
        else (short_instrument.strike if short_instrument is not None else Decimal("0"))
    )
    if strike <= 0 or index_price_usd <= strike:
        return Decimal("0")
    if (
        short_instrument is not None
        and short_instrument.strike == strike
        and not (
            short_instrument.quote_currency.upper() == "USDC" and short_instrument.settlement_currency.upper() == "USDC"
        )
    ):
        per_unit = _intrinsic_settlement(short_instrument, shocked_spot=index_price_usd, option_type="call")
    else:
        intrinsic_usd = max(index_price_usd - strike, Decimal("0"))
        per_unit = intrinsic_usd / index_price_usd
    return max(per_unit * group.quantity, Decimal("0"))


def covered_call_settlement_loss_from_transaction_log(
    client: Any,
    *,
    currency: str,
    instrument_name: str,
    expiration_timestamp_ms: int,
    window_ms: int = DEFAULT_SETTLEMENT_LOG_WINDOW_MS,
) -> Decimal | None:
    """Sum settlement/delivery outflows for one option; ``None`` when log has no matching rows."""
    if client is None or not instrument_name or expiration_timestamp_ms <= 0:
        return None
    if not hasattr(client, "iter_transaction_log"):
        return None
    start = max(0, int(expiration_timestamp_ms) - window_ms)
    end = int(expiration_timestamp_ms) + window_ms
    outflow = Decimal("0")
    saw_row = False
    try:
        for row in client.iter_transaction_log(
            currency=currency.upper(),
            start_timestamp=start,
            end_timestamp=end,
            count=100,
        ):
            if str(row.get("instrument_name") or "") != instrument_name:
                continue
            entry_type = str(row.get("type") or "").lower()
            if entry_type not in SETTLEMENT_LOG_TYPES:
                continue
            saw_row = True
            amount_raw = row.get("change")
            if amount_raw is None:
                amount_raw = row.get("amount")
            change = to_decimal(amount_raw)
            if change < 0:
                outflow += -change
    except Exception:
        return None
    if not saw_row:
        return None
    return outflow


def resolve_covered_call_settlement_loss(
    group: TradeGroup,
    *,
    index_price_usd: Decimal,
    short_instrument: OptionInstrument | None,
    client: Any | None,
    reason: str = "",
    prefer_log: bool = False,
) -> tuple[Decimal, str]:
    """Return ``(loss_native, source)`` where source is ``skipped_robust`` | ``transaction_log`` | ``intrinsic``."""
    if covered_call_spot_exit_skips_settlement_loss(reason=reason, spot_exit_reason=group.spot_exit_reason):
        return Decimal("0"), "skipped_robust"
    if prefer_log and client is not None:
        from_log = covered_call_settlement_loss_from_transaction_log(
            client,
            currency=group.currency,
            instrument_name=group.short_instrument_name,
            expiration_timestamp_ms=int(group.expiration_timestamp_ms or 0),
        )
        if from_log is not None:
            return from_log, "transaction_log"
    intrinsic = covered_call_settlement_loss_from_intrinsic(
        group,
        index_price_usd=index_price_usd,
        short_instrument=short_instrument,
    )
    return intrinsic, "intrinsic"
