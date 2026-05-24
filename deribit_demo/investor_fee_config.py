"""Investor-level fee configuration (NAV_perf / HWM / performance fee rates).

Values live in ``config/investors/<id>/.env.investor`` (or legacy ``investor.env``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

from .env_layout import resolve_investor_env_path
from .utils import to_decimal


@dataclass(frozen=True)
class InvestorFeeConfig:
    collateral_spot_btc: Decimal
    collateral_spot_eth: Decimal
    performance_fee_rate: Decimal
    management_fee_annual_rate: Decimal
    initial_hwm_nav_perf: Decimal | None


def _optional_decimal(values: dict[str, str | None], key: str, default: str = "0") -> Decimal:
    raw = values.get(key)
    if raw is None or str(raw).strip() == "":
        return to_decimal(default)
    return to_decimal(raw)


def load_investor_fee_config(investor_dir: Path) -> InvestorFeeConfig:
    """Load fee settings from ``.env.investor`` when present; otherwise use documented defaults."""
    env_path = resolve_investor_env_path(investor_dir)
    values: dict[str, str | None] = dict(dotenv_values(env_path)) if env_path is not None else {}

    initial_raw = values.get("INITIAL_HWM_NAV_PERF")
    initial_hwm = None
    if initial_raw is not None and str(initial_raw).strip():
        initial_hwm = to_decimal(initial_raw)

    return InvestorFeeConfig(
        collateral_spot_btc=_optional_decimal(values, "COLLATERAL_SPOT_BTC"),
        collateral_spot_eth=_optional_decimal(values, "COLLATERAL_SPOT_ETH"),
        performance_fee_rate=_optional_decimal(values, "PERFORMANCE_FEE_RATE", "0.10"),
        management_fee_annual_rate=_optional_decimal(values, "MANAGEMENT_FEE_ANNUAL_RATE", "0.01"),
        initial_hwm_nav_perf=initial_hwm,
    )
