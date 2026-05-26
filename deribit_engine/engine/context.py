from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..models import (
    AccountSummary,
    OpenOrder,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    Position,
    RiskRegime,
    StrategyState,
)

LOGGER = logging.getLogger(__name__)
LOG_REASON_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
# Defer reconciled_external closes right after entry while Deribit positions API catches up.
RECONCILE_EXTERNAL_CLOSE_GRACE_MS = 180_000
_TELEGRAM_CLOSE_REASONS = frozenset(
    {"hard_stop", "panic_close", "soft_stop", "soft_stop_no_hedge", "covered_call_robust_exit"}
)
# When logging scan blockers, avoid megabytes of text per cycle.
_MAX_SCAN_BLOCKER_LOG_LINES = 36
# Append at most this many `example_messages` from scan rejection detail per side.
_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES = 10


@dataclass(frozen=True)
class ExchangePrefetch:
    """Deribit account snapshot shared across strategy state files on the same login."""

    summaries: dict[str, AccountSummary]
    open_orders: list[OpenOrder]
    positions: list[Position]
    option_positions: list[Position]
    future_positions: list[Position]
    future_markets_by_name: dict[str, OptionInstrument]
    markets_by_currency: dict[str, list[OptionInstrument]]


@dataclass
class RuntimeContext:
    state: StrategyState
    summaries: dict[str, AccountSummary]
    open_orders: list[OpenOrder]
    positions: list[Position]
    option_positions: list[Position]
    future_positions: list[Position]
    future_markets_by_name: dict[str, OptionInstrument]
    markets_by_currency: dict[str, list[OptionInstrument]]
    orderbook_cache: dict[str, OrderBookSnapshot]
    regime_by_currency: dict[str, RiskRegime]
    snapshot: PortfolioSnapshot
