"""Deribit trading-fee discount window (e.g. 10% off for 6 months).

Default anchor is Deribit account registration (``OPTION_FEE_DISCOUNT_REGISTRATION_MS``).
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .investor_cash_flow import default_fee_flow_start_ms
from .models import TradeGroup
from .state import StrategyStateStore
from .trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state

if TYPE_CHECKING:
    from .config import BotConfig

LOGGER = logging.getLogger(__name__)

ANCHOR_FIRST_TRADE = "first_trade"
ANCHOR_REGISTRATION = "registration"

_DEFAULT_DISCOUNT_MONTHS = 6


@dataclass
class FeeDiscountContext:
    """Single source of truth for the option fee-discount rate.

    Both screening (``StrategySelector``) and execution (``EngineBase``) resolve
    the discount through one shared instance so scan economics match live fills.
    The engine lazily populates ``first_trade_timestamp_ms`` from
    state/journal/exchange; the strategy reads it back through the same object.
    """

    base_rate: Decimal
    discount_months: int
    anchor: str
    registration_timestamp_ms: int | None
    first_trade_timestamp_ms: int | None = None

    @classmethod
    def from_config(cls, config: BotConfig) -> FeeDiscountContext:
        return cls(
            base_rate=config.option_fee_discount_rate,
            discount_months=config.option_fee_discount_months,
            anchor=config.option_fee_discount_anchor,
            registration_timestamp_ms=config.option_fee_discount_registration_ms or None,
        )

    def rate_at(self, at_timestamp_ms: int) -> Decimal:
        return effective_option_fee_discount_rate(
            base_rate=self.base_rate,
            discount_months=self.discount_months,
            first_trade_timestamp_ms=self.first_trade_timestamp_ms,
            anchor=self.anchor,
            registration_timestamp_ms=self.registration_timestamp_ms,
            at_timestamp_ms=at_timestamp_ms,
        )


def add_calendar_months_ms(timestamp_ms: int, months: int) -> int:
    """UTC calendar month add (handles month-end clamping)."""
    if months == 0:
        return timestamp_ms
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    new_dt = dt.replace(year=year, month=month, day=day)
    return int(new_dt.timestamp() * 1000)


def discount_window_end_ms(
    *,
    anchor_timestamp_ms: int,
    discount_months: int,
) -> int:
    return add_calendar_months_ms(anchor_timestamp_ms, discount_months)


def is_fee_discount_active(
    *,
    discount_months: int,
    anchor_timestamp_ms: int | None,
    at_timestamp_ms: int,
) -> bool:
    if discount_months <= 0 or anchor_timestamp_ms is None or anchor_timestamp_ms <= 0:
        return False
    end_ms = discount_window_end_ms(
        anchor_timestamp_ms=anchor_timestamp_ms,
        discount_months=discount_months,
    )
    return anchor_timestamp_ms <= at_timestamp_ms < end_ms


def resolve_discount_anchor_ms(
    *,
    anchor: str,
    first_trade_timestamp_ms: int | None,
    registration_timestamp_ms: int | None,
    at_timestamp_ms: int,
) -> int | None:
    """Return window start ms; ``first_trade`` falls back to ``at_timestamp_ms`` when unknown."""
    mode = (anchor or ANCHOR_REGISTRATION).strip().lower()
    if mode == ANCHOR_REGISTRATION:
        if registration_timestamp_ms is not None and registration_timestamp_ms > 0:
            return registration_timestamp_ms
        return None
    if first_trade_timestamp_ms is not None and first_trade_timestamp_ms > 0:
        return first_trade_timestamp_ms
    return at_timestamp_ms


def effective_option_fee_discount_rate(
    *,
    base_rate: Decimal,
    discount_months: int,
    at_timestamp_ms: int,
    first_trade_timestamp_ms: int | None = None,
    anchor: str = ANCHOR_REGISTRATION,
    registration_timestamp_ms: int | None = None,
) -> Decimal:
    """Fee discount rate applicable at ``at_timestamp_ms`` (0 if outside the window)."""
    if base_rate <= 0 or discount_months <= 0:
        return Decimal("0")
    anchor_ms = resolve_discount_anchor_ms(
        anchor=anchor,
        first_trade_timestamp_ms=first_trade_timestamp_ms,
        registration_timestamp_ms=registration_timestamp_ms,
        at_timestamp_ms=at_timestamp_ms,
    )
    if not is_fee_discount_active(
        discount_months=discount_months,
        anchor_timestamp_ms=anchor_ms,
        at_timestamp_ms=at_timestamp_ms,
    ):
        return Decimal("0")
    return base_rate


def _min_positive_ms(*values: int | None) -> int | None:
    candidates = [int(v) for v in values if v is not None and int(v) > 0]
    return min(candidates) if candidates else None


def first_option_trade_timestamp_ms_from_groups(groups: Iterable[TradeGroup]) -> int | None:
    candidates: list[int] = []
    for group in groups:
        if group.entry_timestamp_ms > 0:
            candidates.append(int(group.entry_timestamp_ms))
        if group.closed_timestamp_ms is not None and group.closed_timestamp_ms > 0:
            candidates.append(int(group.closed_timestamp_ms))
    return min(candidates) if candidates else None


def first_option_trade_timestamp_ms_from_journal(
    journal: TradeJournalStore,
    scope_key: str,
) -> int | None:
    rows = journal.list_executions(scope_key, limit=5000)
    candidates: list[int] = []
    for row in rows:
        ts = int(row.get("ts_ms") or 0)
        if ts <= 0:
            continue
        inst = str(row.get("instrument_name") or "")
        if inst.startswith("BTC-") or inst.startswith("ETH-"):
            candidates.append(ts)
    return min(candidates) if candidates else None


def first_option_trade_timestamp_ms_from_client(
    client: Any,
    *,
    currencies: tuple[str, ...] = ("BTC", "ETH"),
    start_timestamp_ms: int | None = None,
) -> int | None:
    start_ms = start_timestamp_ms if start_timestamp_ms is not None else default_fee_flow_start_ms()
    candidates: list[int] = []
    for currency in currencies:
        ccy = currency.upper()
        try:
            rows = client.iter_transaction_log(
                currency=ccy,
                start_timestamp=start_ms,
                end_timestamp=9999999999999,
                count=100,
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("transaction log scan failed for %s", ccy, exc_info=True)
            continue
        for row in rows:
            if str(row.get("type") or "").lower() != "trade":
                continue
            inst = str(row.get("instrument_name") or "")
            if not (inst.startswith("BTC-") or inst.startswith("ETH-")):
                continue
            ts = int(row.get("timestamp") or 0)
            if ts > 0:
                candidates.append(ts)
    return min(candidates) if candidates else None


def resolve_first_option_trade_timestamp_ms(
    *,
    state_path: Path | str | None = None,
    client: Any | None = None,
    start_timestamp_ms: int | None = None,
) -> int | None:
    """Earliest option trade ts from state groups, trade journal, and/or Deribit log."""
    candidates: list[int] = []
    if state_path is not None:
        path = Path(state_path)
        if path.is_file():
            try:
                store = StrategyStateStore(path)
                state = store.load()
                from_groups = first_option_trade_timestamp_ms_from_groups(state.groups)
                if from_groups is not None:
                    candidates.append(from_groups)
            except Exception:  # noqa: BLE001
                LOGGER.debug("state scan failed for %s", path, exc_info=True)
            journal_path = journal_db_path_for_state(path)
            if journal_path.is_file():
                try:
                    journal = TradeJournalStore(journal_path)
                    from_journal = first_option_trade_timestamp_ms_from_journal(
                        journal,
                        scope_key_for_state(path),
                    )
                    if from_journal is not None:
                        candidates.append(from_journal)
                except Exception:  # noqa: BLE001
                    LOGGER.debug("journal scan failed for %s", path, exc_info=True)
    if client is not None:
        from_client = first_option_trade_timestamp_ms_from_client(
            client,
            start_timestamp_ms=start_timestamp_ms,
        )
        if from_client is not None:
            candidates.append(from_client)
    return _min_positive_ms(*candidates)
