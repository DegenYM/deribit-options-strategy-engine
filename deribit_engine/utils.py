from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal, getcontext
from enum import Enum
from typing import Any

getcontext().prec = 28

ZERO = Decimal("0")
ONE = Decimal("1")

OPTION_NAME_RE = re.compile(
    r"^(?P<symbol>[A-Z]+(?:_[A-Z]+)*)-(?P<date>\d{2}[A-Z]{3}\d{2})-(?P<strike>\d+(?:\.\d+)?)-(?P<kind>[CP])$"
)


def to_decimal(value: Any, default: Decimal = ZERO) -> Decimal:
    if value in (None, ""):
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return default


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_UP)
    return units * step


def decimal_gcd(a: Decimal, b: Decimal) -> Decimal:
    a, b = abs(a), abs(b)
    if a == 0:
        return b
    if b == 0:
        return a
    while b != 0:
        a, b = b, a % b
    return a


def decimal_lcm(a: Decimal, b: Decimal) -> Decimal:
    """Least common multiple of two positive decimals (exact for typical Deribit contract sizes)."""
    if a <= 0 and b <= 0:
        return Decimal("0.01")
    if a <= 0:
        return b
    if b <= 0:
        return a
    g = decimal_gcd(a, b)
    if g == 0:
        return max(a, b)
    return abs(a * b) / g


def option_spread_amount_step(
    contract_size_short: Decimal,
    contract_size_long: Decimal,
    _min_trade_short: Decimal,
    _min_trade_long: Decimal,
) -> Decimal:
    """Quantity step for a vertical spread: lcm of both legs' contract_size (same amount on each leg)."""
    cs_s = contract_size_short if contract_size_short > 0 else Decimal("0.01")
    cs_l = contract_size_long if contract_size_long > 0 else Decimal("0.01")
    return decimal_lcm(cs_s, cs_l)


def option_spread_min_valid_quantity(
    contract_size_short: Decimal,
    contract_size_long: Decimal,
    min_trade_short: Decimal,
    min_trade_long: Decimal,
) -> Decimal:
    """Smallest spread quantity that satisfies min_trade on each leg and both contract_size grids."""
    cs_s = contract_size_short if contract_size_short > 0 else Decimal("0.01")
    cs_l = contract_size_long if contract_size_long > 0 else Decimal("0.01")
    step = decimal_lcm(cs_s, cs_l)
    min_s = ceil_to_step(min_trade_short, cs_s) if min_trade_short > 0 else cs_s
    min_l = ceil_to_step(min_trade_long, cs_l) if min_trade_long > 0 else cs_l
    need = max(min_s, min_l)
    return ceil_to_step(need, step)


def align_option_order_amount(amount: Decimal, contract_size: Decimal, min_trade_amount: Decimal) -> Decimal:
    """
    Align Deribit order amount to the exchange grid and minimum size.

    For linear options, `contract_size` can be larger than the tradable amount step, so use the
    smaller positive value between `contract_size` and `min_trade_amount` as the step.
    """
    step_candidates = [value for value in (contract_size, min_trade_amount) if value > 0]
    step = min(step_candidates) if step_candidates else Decimal("0")
    if step <= 0:
        return amount if amount > 0 else Decimal("0")
    aligned = floor_to_step(amount, step)
    if aligned <= 0:
        return Decimal("0")
    if min_trade_amount > 0 and aligned < min_trade_amount:
        return Decimal("0")
    return aligned


def safe_div(numerator: Decimal, denominator: Decimal, default: Decimal = ZERO) -> Decimal:
    if denominator == 0:
        return default
    return numerator / denominator


def format_decimal(value: Decimal | None, places: int = 8) -> str | None:
    if value is None:
        return None
    quant = Decimal("1").scaleb(-places)
    rendered = value.quantize(quant, rounding=ROUND_DOWN)
    text = format(rendered.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def ms_to_datetime(value: int | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def datetime_to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_ms() -> int:
    return datetime_to_ms(utc_now()) or 0


def dte_days(expiry_ms: int | str | None, now: datetime | None = None) -> Decimal:
    expiry = ms_to_datetime(expiry_ms)
    if expiry is None:
        return ZERO
    current = now or utc_now()
    return Decimal(str((expiry - current).total_seconds())) / Decimal("86400")


def parse_csv(value: str | None, *, upper: bool = False) -> tuple[str, ...]:
    if not value:
        return ()
    items = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        items.append(item.upper() if upper else item)
    return tuple(items)


def parse_option_name(instrument_name: str) -> dict[str, str] | None:
    match = OPTION_NAME_RE.match(instrument_name)
    if not match:
        return None
    data = match.groupdict()
    data["option_type"] = "call" if data["kind"] == "C" else "put"
    symbol = data["symbol"]
    data["base_currency"] = symbol.split("_", 1)[0]
    data["quote_currency"] = symbol.split("_", 1)[1] if "_" in symbol else ""
    return data


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def dumps_json(value: Any) -> str:
    return json.dumps(value, default=json_default, ensure_ascii=False, indent=2, sort_keys=True)
