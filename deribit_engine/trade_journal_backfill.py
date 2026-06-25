"""Backfill trade_journal SQLite from Deribit API fills and/or local strategy state."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .bull_put_settlement import (
    repair_bull_put_expiry_reconcile_pnl,
    restore_long_leg_from_journal_executions,
    settlement_index_usd_for_group,
)
from .client import DeribitClient
from .config import BotConfig, load_config
from .exit_reasons import INCOME_EXIT_REASONS
from .fee_discount import effective_option_fee_discount_rate, resolve_first_option_trade_timestamp_ms
from .fees import option_trade_fee_native, option_trade_fee_usdc, premium_value_usdc
from .metrics_store import MetricsStore, performance_scope_key
from .models import TradeGroup
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .trade_journal import (
    TradeJournalStore,
    _fee_usdc_from_trade,
    _trade_id,
    journal_db_path_for_state,
    scope_key_for_state,
)
from .utils import parse_option_name, safe_div, to_decimal, utc_now_ms

LOGGER = logging.getLogger(__name__)


def _default_metrics_db_for_env(env_file: Path) -> Path:
    from .env_layout import find_repo_root, investor_id_from_account_env, investor_metrics_db_path

    repo_root = find_repo_root(env_file)
    investor_id = investor_id_from_account_env(env_file, repo_root=repo_root)
    if investor_id and repo_root is not None:
        return investor_metrics_db_path(repo_root, investor_id)
    if investor_id:
        return Path("data/frontend_ledger") / investor_id / "metrics.db"
    return Path("data/frontend_ledger") / "metrics.db"


_LABEL_RE = re.compile(
    r"^(?P<prefix>.+)-spread-(?P<ccy>[a-z]+)-(?P<gid>\d+)-(?P<leg>short|long)(?:-(?P<close>close))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BackfillSummary:
    env_file: str
    state_file: str
    journal_db: str
    api_trades_seen: int
    api_inserted: int
    api_skipped_label: int
    state_groups: int
    state_groups_skipped: int
    state_inserted: int
    metrics_synced: bool
    coin_groups_recalculated: int = 0
    coin_close_repaired: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_file": self.env_file,
            "state_file": self.state_file,
            "journal_db": self.journal_db,
            "api_trades_seen": self.api_trades_seen,
            "api_inserted": self.api_inserted,
            "api_skipped_label": self.api_skipped_label,
            "state_groups": self.state_groups,
            "state_groups_skipped": self.state_groups_skipped,
            "state_inserted": self.state_inserted,
            "metrics_synced": self.metrics_synced,
            "coin_groups_recalculated": self.coin_groups_recalculated,
            "coin_close_repaired": self.coin_close_repaired,
        }


@dataclass
class _ScopeAccount:
    name: str
    state_path: Path


def _state_fingerprint(state_path: Path, closed_count: int) -> str:
    try:
        mtime = state_path.stat().st_mtime if state_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return json.dumps(
        {"state": str(state_path.resolve()), "mtime": mtime, "closed_count": closed_count},
        sort_keys=True,
    )


def load_trade_groups(state_path: Path) -> list[TradeGroup]:
    if not state_path.is_file():
        return []
    store = StrategyStateStore(state_path)
    state = store.load()
    excluded = load_performance_exclusion_group_ids(state_path)
    return [g for g in state.groups if g.group_id and g.group_id not in excluded]


def _parse_bot_label(label: str, *, label_prefix: str) -> tuple[str, str, str] | None:
    """Return (group_id, leg, event_type) when label matches bot convention."""
    text = str(label or "").strip()
    if not text:
        return None
    match = _LABEL_RE.match(text)
    if not match:
        return None
    prefix = match.group("prefix")
    if prefix != label_prefix:
        return None
    group_id = str(match.group("gid")).zfill(4) if match.group("gid").isdigit() else str(match.group("gid"))
    leg = match.group("leg").lower()
    event_type = "close" if match.group("close") else "open"
    return group_id, leg, event_type


def _group_has_journal(store: TradeJournalStore, scope_key: str, group_id: str) -> bool:
    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            """
            SELECT 1 FROM trade_executions
            WHERE scope_key = ? AND group_id = ?
            LIMIT 1
            """,
            (scope_key, group_id),
        ).fetchone()
    return row is not None


def _synthetic_trade_id(group_id: str, event_type: str, leg: str, instrument: str) -> str:
    return f"backfill-state:{group_id}:{event_type}:{leg}:{instrument}"


def _state_synthetic_premium_price(
    gross_usdc: Decimal,
    qty: Decimal,
    group: TradeGroup,
) -> Decimal | None:
    """Coin options: store coin premium per contract, not USDC/qty."""
    if qty <= 0 or gross_usdc <= 0:
        return None
    if not group.is_coin_collateral():
        return safe_div(gross_usdc, qty)
    idx = group.entry_index_usd if group.entry_index_usd > 0 else None
    if idx is None and group.close_index_usd is not None and group.close_index_usd > 0:
        idx = group.close_index_usd
    if idx is None or idx <= 0:
        return None
    return safe_div(gross_usdc, qty * idx)


@dataclass(frozen=True)
class StateGroupStatsBackfillSummary:
    state_file: str
    closed_groups: int
    pnl_updated: int
    apr_updated: int
    entry_apr_updated: int
    saved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_file": self.state_file,
            "closed_groups": self.closed_groups,
            "pnl_updated": self.pnl_updated,
            "apr_updated": self.apr_updated,
            "entry_apr_updated": self.entry_apr_updated,
            "saved": self.saved,
        }


_DEFAULT_OPTION_FEE_RATE = Decimal("0.0003")
_DEFAULT_OPTION_FEE_CAP_RATE = Decimal("0.125")


def _is_covered_call_group(group: TradeGroup) -> bool:
    return group.is_covered_call_group()


def _account_env_candidates_for_state(state_path: Path) -> list[Path]:
    from .env_layout import find_repo_root

    repo = find_repo_root(state_path)
    stem = state_path.stem
    candidates: list[Path] = []
    if repo is not None:
        parent = state_path.resolve().parent
        if parent.parent.name == "investors":
            investor_id = parent.name
            candidates.append(repo / "config" / "investors" / investor_id / "accounts" / f".env.{stem}")
        candidates.append(repo / "config" / "shared" / "strategies" / f".env.{stem}")
        candidates.append(repo / f".env.{stem}")
    return candidates


def _config_for_state(state_path: Path) -> BotConfig | None:
    """Best-effort account config from a sibling ``.env.<slug>`` file."""
    try:
        for env_path in _account_env_candidates_for_state(state_path):
            if env_path.is_file():
                return load_config(env_path, require_private=False)
    except Exception:  # noqa: BLE001
        pass
    return None


def _fee_rates_for_state(state_path: Path) -> tuple[Decimal, Decimal, Decimal]:
    """Best-effort fee rates from a sibling account env; else Deribit defaults."""
    try:
        cfg = _config_for_state(state_path)
        if cfg is not None:
            return cfg.option_fee_rate, cfg.option_fee_cap_rate, cfg.option_fee_discount_rate
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_OPTION_FEE_RATE, _DEFAULT_OPTION_FEE_CAP_RATE, Decimal("0")


def repair_coin_fee_collateral_for_state(
    state_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Recompute coin ``*_fee_collateral`` and ``realized_pnl_collateral_native`` using account fee discount."""
    cfg = _config_for_state(state_path)
    base_discount = cfg.option_fee_discount_rate if cfg is not None else Decimal("0")
    discount_months = cfg.option_fee_discount_months if cfg is not None else 6
    anchor = cfg.option_fee_discount_anchor if cfg is not None else "registration"
    registration_ms = cfg.option_fee_discount_registration_ms if cfg is not None else 0
    fee_rate = cfg.option_fee_rate if cfg is not None else _DEFAULT_OPTION_FEE_RATE
    fee_cap = cfg.option_fee_cap_rate if cfg is not None else _DEFAULT_OPTION_FEE_CAP_RATE

    store = StrategyStateStore(state_path)
    state = store.load()
    first_trade_ms = resolve_first_option_trade_timestamp_ms(state_path=state_path)
    updated = 0
    for group in state.groups:
        if group.status != "closed" or not group.is_coin_collateral():
            continue
        qty = group.quantity if group.quantity > 0 else Decimal("0")
        if qty <= 0:
            continue
        book = group.collateral_currency or group.currency or "BTC"
        entry_px = group.short_entry_average_price
        close_px = group.short_close_average_price or Decimal("0")
        if entry_px <= 0 or close_px <= 0:
            continue
        entry_idx = group.entry_index_usd if group.entry_index_usd > 0 else group.close_index_usd
        close_idx = (
            group.close_index_usd if group.close_index_usd is not None and group.close_index_usd > 0 else entry_idx
        )
        entry_ms = int(group.entry_timestamp_ms or 0)
        close_ms = int(group.closed_timestamp_ms or entry_ms or 0)
        entry_discount = effective_option_fee_discount_rate(
            base_rate=base_discount,
            discount_months=discount_months,
            first_trade_timestamp_ms=first_trade_ms,
            anchor=anchor,
            registration_timestamp_ms=registration_ms or None,
            at_timestamp_ms=entry_ms if entry_ms > 0 else close_ms,
        )
        close_discount = effective_option_fee_discount_rate(
            base_rate=base_discount,
            discount_months=discount_months,
            first_trade_timestamp_ms=first_trade_ms,
            anchor=anchor,
            registration_timestamp_ms=registration_ms or None,
            at_timestamp_ms=close_ms if close_ms > 0 else entry_ms,
        )
        entry_fee_collateral = option_trade_fee_native(
            index_price=entry_idx,
            premium=entry_px,
            quantity=qty,
            fee_rate=fee_rate,
            fee_cap_rate=fee_cap,
            quote_currency="",
            settlement_currency=book,
            fee_discount_rate=entry_discount,
        )
        close_fee_collateral = option_trade_fee_native(
            index_price=close_idx,
            premium=close_px,
            quantity=qty,
            fee_rate=fee_rate,
            fee_cap_rate=fee_cap,
            quote_currency="",
            settlement_currency=book,
            fee_discount_rate=close_discount,
        )
        before_native = group.realized_pnl_collateral_native
        group.entry_fee_collateral = entry_fee_collateral
        group.close_fee_collateral = close_fee_collateral
        if entry_idx > 0:
            group.entry_fee = entry_fee_collateral * entry_idx
        if close_idx > 0:
            group.realized_close_fee = close_fee_collateral * close_idx
        group.backfill_realized_pnl_collateral_native(
            spot_index_usd=close_idx if close_idx > 0 else None,
        )
        if group.realized_pnl_collateral_native != before_native or group.entry_fee_collateral != entry_fee_collateral:
            updated += 1

    if updated and not dry_run:
        store.save(state)
    return {"groups_updated": updated, "dry_run": int(dry_run)}


def infer_bot_income_exit_close_reason(
    executions: list[dict[str, Any]],
    *,
    order_label_prefix: str,
) -> str | None:
    """Recover income-exit reason when exchange closed via bot API but state reconcile ran."""
    prefix = str(order_label_prefix or "").strip()
    if not prefix:
        return None
    for row in executions:
        if str(row.get("event_type") or "").lower() != "close":
            continue
        if str(row.get("leg") or "short").lower() not in {"", "short", "short_leg"}:
            continue
        if str(row.get("source_action") or "") == "reconcile_external":
            continue
        label = str(row.get("label") or "")
        parsed = _parse_bot_label(label, label_prefix=prefix)
        if parsed is None or parsed[2] != "close":
            continue
        if not str(row.get("order_id") or "").strip():
            continue
        reason = str(row.get("reason") or "").lower()
        if reason in INCOME_EXIT_REASONS:
            return reason
        return "take_profit"
    return None


def profit_sweep_order_label(order_label_prefix: str, group: TradeGroup) -> str:
    return f"{order_label_prefix}-profit-sweep-{group.currency.lower()}-{group.group_id}"


def _order_state_by_label_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        nested = raw.get("order")
        if isinstance(nested, dict):
            return [nested]
        if raw.get("order_id") or raw.get("order_state"):
            return [raw]
        orders = raw.get("orders")
        if isinstance(orders, list):
            return [row for row in orders if isinstance(row, dict)]
    return []


def _profit_sweep_state_locked(group: TradeGroup) -> bool:
    reason = str(group.profit_sweep_reason or "")
    if group_excluded_from_premium_proceeds_pool(group):
        return True
    if "duplicate_sweep_repaired" in reason:
        return True
    if "premium_amount_synced" in reason:
        return True
    if "proceeds_reconciled" in reason:
        from .profit_sweep_ops import profit_sweep_has_exchange_fill

        return profit_sweep_has_exchange_fill(group)
    return False


def reconcile_profit_sweep_from_exchange(
    group: TradeGroup,
    *,
    client: DeribitClient,
    order_label_prefix: str,
    trade_cache: Any | None = None,
) -> bool:
    """Sync filled profit-sweep orders from Deribit when state still shows pending."""
    if group.status != "closed" or not _is_covered_call_group(group):
        return False
    if _profit_sweep_state_locked(group):
        return False

    label = profit_sweep_order_label(order_label_prefix, group)
    trades: list[dict[str, Any]] = []
    if trade_cache is not None:
        trades = list(trade_cache.trades_for_group(group, order_label_prefix))
    else:
        fetch_trades = getattr(client, "get_user_trades_by_currency", None)
        if callable(fetch_trades):
            try:
                seen: set[Any] = set()
                for trade in fetch_trades(group.currency, kind="spot", count=100, historical=True).get("trades", []):
                    if str(trade.get("label") or "") != label:
                        continue
                    if str(trade.get("direction") or "").lower() != "sell":
                        continue
                    trade_id = trade.get("trade_id")
                    if trade_id in seen:
                        continue
                    seen.add(trade_id)
                    trades.append(trade)
            except Exception:  # noqa: BLE001
                LOGGER.debug(
                    "profit_sweep reconcile: currency trades lookup failed group=%s",
                    group.group_id,
                    exc_info=True,
                )
                trades = []

    if trades:
        trades.sort(key=lambda row: int(row.get("timestamp") or 0))
        days = sorted(
            {
                datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000, tz=UTC).strftime("%Y-%m-%d")
                for row in trades
            }
        )
        first_day = days[0]
        first_trades = [
            row
            for row in trades
            if datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000, tz=UTC).strftime("%Y-%m-%d") == first_day
        ]
        amount = sum(to_decimal(row.get("amount")) for row in first_trades)
        from .wallet_ops import spot_sell_quote_proceeds_from_trades

        proceeds = spot_sell_quote_proceeds_from_trades(first_trades, quote_currency="USDT")
        last = first_trades[-1]
        order_id = str(last.get("order_id") or "").strip()
        instrument_name = str(last.get("instrument_name") or f"{group.currency.upper()}_USDT")

        group.profit_sweep_status = "filled"
        group.profit_sweep_instrument_name = instrument_name
        if order_id:
            group.profit_sweep_order_id = order_id
        if amount > 0:
            group.profit_sweep_amount = amount
            group.profit_sweep_exchange_native = amount
        if proceeds > 0:
            group.profit_sweep_quote_proceeds = proceeds
            from .profit_sweep_ops import record_profit_sweep_lifetime_proceeds

            record_profit_sweep_lifetime_proceeds(group, proceeds)
        reason = str(group.profit_sweep_reason or "")
        if len(days) > 1 and "duplicate_sweep_detected" not in reason:
            group.profit_sweep_reason = (reason + "; duplicate_sweep_detected").strip("; ")
        elif not reason:
            group.profit_sweep_reason = "profit_sweep"
        return True

    if group.profit_sweep_status == "filled" and group.profit_sweep_order_id and group.profit_sweep_quote_proceeds > 0:
        return False
    try:
        raw = client.get_order_state_by_label(group.currency, label)
    except Exception:  # noqa: BLE001
        LOGGER.debug("profit_sweep reconcile: label lookup failed group=%s", group.group_id, exc_info=True)
        return False
    filled: dict[str, Any] | None = None
    for order in _order_state_by_label_rows(raw):
        if str(order.get("order_state") or "").lower() == "filled":
            filled = order
            break
    if filled is None:
        return False
    order_id = str(filled.get("order_id") or "").strip()
    instrument_name = str(filled.get("instrument_name") or f"{group.currency.upper()}_USDT")
    amount = to_decimal(filled.get("filled_amount") or filled.get("amount"))
    group.profit_sweep_status = "filled"
    group.profit_sweep_instrument_name = instrument_name
    if order_id:
        group.profit_sweep_order_id = order_id
    if amount > 0:
        group.profit_sweep_amount = amount
        group.profit_sweep_exchange_native = amount
    proceeds = Decimal("0")
    if order_id:
        try:
            from .wallet_ops import spot_sell_quote_proceeds_from_trades

            trades = client.get_user_trades_by_order(order_id)
            proceeds = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
        except Exception:  # noqa: BLE001
            LOGGER.debug(
                "profit_sweep reconcile: trades lookup failed order=%s group=%s",
                order_id,
                group.group_id,
                exc_info=True,
            )
    if proceeds <= 0:
        avg = to_decimal(filled.get("average_price"))
        if avg > 0 and amount > 0:
            proceeds = amount * avg
    if proceeds > 0:
        group.profit_sweep_quote_proceeds = proceeds
        from .profit_sweep_ops import record_profit_sweep_lifetime_proceeds

        record_profit_sweep_lifetime_proceeds(group, proceeds)
    if not group.profit_sweep_reason:
        group.profit_sweep_reason = "profit_sweep"
    return True


_UNLABELED_PREMIUM_REPAIR_REASON = "unlabeled_premium_reconciled"
_UNLABELED_PREMIUM_MATCH_WINDOW_MS = 7 * 86400 * 1000


def group_excluded_from_premium_proceeds_pool(group: TradeGroup) -> bool:
    """Groups with exchange-attributed unlabeled premium sells skip dust-pool reconcile."""
    reason = str(group.profit_sweep_reason or "")
    if _UNLABELED_PREMIUM_REPAIR_REASON in reason:
        return True
    # Pre-label manual premium swaps must not be re-weighted by dust-pool reconcile.
    if "manual_swap" in reason:
        return True
    return False


def unlabeled_premium_usdt_total(groups: list[TradeGroup]) -> Decimal:
    """Sum of USDT proceeds attributed to pre-label premium spot sells."""
    total = Decimal("0")
    for group in groups:
        if group.status != "closed" or not group_excluded_from_premium_proceeds_pool(group):
            continue
        proceeds = group.profit_sweep_quote_proceeds_lifetime or group.profit_sweep_quote_proceeds
        if proceeds > 0:
            total += proceeds
    return total


def _manual_swap_usdt_proceeds_estimate(group: TradeGroup) -> Decimal | None:
    if group.status != "closed" or not _is_covered_call_group(group):
        return None
    if "manual_swap" not in str(group.profit_sweep_reason or ""):
        return None
    native = group.realized_pnl_collateral_native
    if native is None or native <= 0:
        return None
    pnl = group.realized_pnl
    if pnl is not None and pnl > 0:
        return pnl
    spot = group.close_index_usd or group.entry_index_usd
    if spot is None or spot <= 0:
        return None
    return native * spot


def repair_manual_swap_proceeds_in_groups(groups: list[TradeGroup]) -> int:
    """Restore USDT proceeds for pre-label manual premium swaps (no exchange API)."""
    repaired = 0
    for group in groups:
        estimate = _manual_swap_usdt_proceeds_estimate(group)
        if estimate is None or estimate <= 0:
            continue
        current = group.profit_sweep_quote_proceeds_lifetime or group.profit_sweep_quote_proceeds
        if current >= estimate * Decimal("0.95"):
            continue
        group.profit_sweep_status = "filled"
        if (group.profit_sweep_amount or Decimal("0")) <= 0 and group.realized_pnl_collateral_native:
            group.profit_sweep_amount = group.realized_pnl_collateral_native
        group.profit_sweep_quote_proceeds = estimate
        from .profit_sweep_ops import record_profit_sweep_lifetime_proceeds

        record_profit_sweep_lifetime_proceeds(group, estimate)
        reason = str(group.profit_sweep_reason or "")
        if _UNLABELED_PREMIUM_REPAIR_REASON not in reason:
            group.profit_sweep_reason = (reason + f"; {_UNLABELED_PREMIUM_REPAIR_REASON}").strip("; ")
        repaired += 1
    return repaired


def _premium_sweep_amount_tolerance(native: Decimal) -> Decimal:
    return max(Decimal("1e-8"), native * Decimal("0.001"))


def _iter_spot_sell_trades(client: DeribitClient, currency: str) -> Iterator[dict[str, Any]]:
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return
    cursor_ts = 0
    while True:
        kwargs: dict[str, Any] = {"currency": currency, "kind": "spot", "count": 100, "historical": True}
        if cursor_ts > 0:
            kwargs["start_timestamp"] = cursor_ts
        batch = fetch(**kwargs)
        if not isinstance(batch, dict):
            break
        trades = list(batch.get("trades") or [])
        if not trades:
            break
        for trade in trades:
            if str(trade.get("direction") or "").lower() == "sell":
                yield trade
        if not batch.get("has_more"):
            break
        last_ts = int(trades[-1].get("timestamp") or 0)
        if last_ts <= 0 or last_ts <= cursor_ts:
            break
        cursor_ts = last_ts + 1


def _collect_unlabeled_premium_sell_trades(client: DeribitClient, currency: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in _iter_spot_sell_trades(client, currency):
        label = str(trade.get("label") or "")
        if label and ("profit-sweep" in label or "spot-exit" in label):
            continue
        if "USDT" not in str(trade.get("instrument_name") or ""):
            continue
        out.append(trade)
    return out


def _group_has_labeled_profit_sweep_trades(
    group: TradeGroup,
    client: DeribitClient,
    order_label_prefix: str,
) -> bool:
    label = profit_sweep_order_label(order_label_prefix, group)
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return False
    try:
        batch = fetch(group.currency, kind="spot", count=100, historical=True)
        for trade in (batch.get("trades") if isinstance(batch, dict) else []) or []:
            if str(trade.get("label") or "") != label:
                continue
            if str(trade.get("direction") or "").lower() != "sell":
                continue
            return True
    except Exception:  # noqa: BLE001
        LOGGER.debug(
            "unlabeled premium repair: labeled trade lookup failed group=%s",
            group.group_id,
            exc_info=True,
        )
    return False


def _match_unlabeled_premium_trades_for_group(
    group: TradeGroup,
    trades: list[dict[str, Any]],
    *,
    used_trade_ids: set[Any],
) -> list[dict[str, Any]]:
    native = group.realized_pnl_collateral_native
    if native is None or native <= 0:
        return []
    close_ms = int(group.closed_timestamp_ms or 0)
    tol = _premium_sweep_amount_tolerance(native)
    candidates: list[dict[str, Any]] = []
    for trade in trades:
        trade_id = trade.get("trade_id")
        if trade_id is not None and trade_id in used_trade_ids:
            continue
        amount = to_decimal(trade.get("amount"))
        if abs(amount - native) > tol:
            continue
        ts = int(trade.get("timestamp") or 0)
        if close_ms > 0:
            if ts < close_ms - 86400000:
                continue
            if ts > close_ms + _UNLABELED_PREMIUM_MATCH_WINDOW_MS:
                continue
        candidates.append(trade)
    if not candidates:
        return []
    if close_ms > 0:
        candidates.sort(key=lambda row: abs(int(row.get("timestamp") or 0) - close_ms))
    chosen = candidates[0]
    if chosen.get("trade_id") is not None:
        used_trade_ids.add(chosen.get("trade_id"))
    return [chosen]


def repair_unlabeled_profit_sweep_from_exchange(
    group: TradeGroup,
    *,
    client: DeribitClient,
    order_label_prefix: str,
    unlabeled_trades: list[dict[str, Any]] | None = None,
    used_trade_ids: set[Any] | None = None,
) -> bool:
    """Attribute pre-label spot premium sells to closed groups (counts as realized USDT)."""
    if group.status != "closed" or not _is_covered_call_group(group):
        return False
    native = group.realized_pnl_collateral_native
    if native is None or native <= 0:
        return False
    if _group_has_labeled_profit_sweep_trades(group, client, order_label_prefix):
        return False

    pool = used_trade_ids if used_trade_ids is not None else set()
    if unlabeled_trades is None:
        unlabeled_trades = _collect_unlabeled_premium_sell_trades(client, group.currency.upper())
    matched = _match_unlabeled_premium_trades_for_group(group, unlabeled_trades, used_trade_ids=pool)
    if not matched:
        return False

    from .wallet_ops import spot_sell_quote_proceeds_from_trades

    proceeds = spot_sell_quote_proceeds_from_trades(matched, quote_currency="USDT")
    if proceeds <= 0:
        return False
    if proceeds <= group.profit_sweep_quote_proceeds and group_excluded_from_premium_proceeds_pool(group):
        return False

    amount = sum(to_decimal(row.get("amount")) for row in matched)
    last = matched[-1]
    order_id = str(last.get("order_id") or "").strip()
    instrument_name = str(last.get("instrument_name") or f"{group.currency.upper()}_USDT")

    group.profit_sweep_status = "filled"
    group.profit_sweep_instrument_name = instrument_name
    if order_id:
        group.profit_sweep_order_id = order_id
    if amount > 0:
        group.profit_sweep_amount = amount
        group.profit_sweep_exchange_native = amount
    group.profit_sweep_quote_proceeds = proceeds
    from .profit_sweep_ops import record_profit_sweep_lifetime_proceeds

    record_profit_sweep_lifetime_proceeds(group, proceeds)
    reason = str(group.profit_sweep_reason or "")
    if _UNLABELED_PREMIUM_REPAIR_REASON not in reason:
        group.profit_sweep_reason = (reason + f"; {_UNLABELED_PREMIUM_REPAIR_REASON}").strip("; ")
    return True


def repair_unlabeled_profit_sweeps_in_groups(
    groups: list[TradeGroup],
    client: DeribitClient,
    order_label_prefix: str,
) -> int:
    """Match orphan spot premium sells (no profit-sweep label) back to closed groups."""
    repaired = repair_manual_swap_proceeds_in_groups(groups)
    unlabeled_by_currency: dict[str, list[dict[str, Any]]] = {}
    used_trade_ids: set[Any] = set()
    closed = [
        g
        for g in groups
        if g.status == "closed" and _is_covered_call_group(g) and (g.realized_pnl_collateral_native or Decimal("0")) > 0
    ]
    closed.sort(key=lambda g: int(g.closed_timestamp_ms or 0))
    for group in closed:
        currency = group.currency.upper()
        if currency not in unlabeled_by_currency:
            unlabeled_by_currency[currency] = _collect_unlabeled_premium_sell_trades(client, currency)
        if repair_unlabeled_profit_sweep_from_exchange(
            group,
            client=client,
            order_label_prefix=order_label_prefix,
            unlabeled_trades=unlabeled_by_currency[currency],
            used_trade_ids=used_trade_ids,
        ):
            repaired += 1
    return repaired


def reconcile_profit_sweeps_in_state(
    state_path: Path,
    *,
    client: DeribitClient,
    dry_run: bool = False,
) -> int:
    """Mark pending profit sweeps as filled when Deribit already executed them."""
    store = StrategyStateStore(state_path)
    state = store.load()
    cfg = _config_for_state(state_path)
    prefix = cfg.order_label_prefix if cfg is not None else "covered_call"
    reconciled = 0
    changed = False
    for group in state.groups:
        if reconcile_profit_sweep_from_exchange(
            group,
            client=client,
            order_label_prefix=prefix,
        ):
            reconciled += 1
            changed = True
    if changed and not dry_run:
        store.save(state)
        LOGGER.info("reconciled profit sweeps in %s: count=%s", state_path.name, reconciled)
    return reconciled


def repair_reconciled_bot_income_exit_group(
    group: TradeGroup,
    executions: list[dict[str, Any]],
    *,
    order_label_prefix: str,
    profit_sweep_enabled: bool,
) -> bool:
    """Fix reconciled_external closes that were bot income exits (post-crash reconcile)."""
    if group.status != "closed":
        return False
    if (group.close_reason or "").lower() != "reconciled_external":
        return False
    inferred = infer_bot_income_exit_close_reason(
        executions,
        order_label_prefix=order_label_prefix,
    )
    if not inferred:
        return False
    group.enrich_fill_prices_from_journal(executions)
    group.close_reason = inferred
    group.last_action = inferred
    close_idx = group.close_index_usd
    spot = close_idx if close_idx is not None and close_idx > 0 else None
    group.sync_coin_profit_native(spot_index_usd=spot)
    if profit_sweep_enabled and inferred in INCOME_EXIT_REASONS and not group.profit_sweep_status:
        native = group.realized_pnl_collateral_native
        if native is not None and native > 0:
            group.profit_sweep_status = "pending"
            group.profit_sweep_amount = native
            group.profit_sweep_reason = inferred
    return True


def repair_reconciled_manual_close_pnl(
    group: TradeGroup,
    *,
    client: Any,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
) -> bool:
    """Recompute USDC manual closes tagged reconciled_external from exchange buy fills."""
    if group.status != "closed":
        return False
    if (group.close_reason or "").lower() != "reconciled_external":
        return False
    if group.is_coin_collateral():
        return False
    if not group.short_instrument_name or group.quantity <= 0:
        return False
    closed_ms = int(group.closed_timestamp_ms or 0)
    if closed_ms <= 0:
        return False
    start_ms = max(int(group.entry_timestamp_ms or 0) - 60_000, 0)
    end_ms = closed_ms + 300_000
    try:
        payload = client.get_user_trades_by_instrument(
            group.short_instrument_name,
            start_timestamp=start_ms,
            end_timestamp=end_ms,
            count=200,
            sorting="asc",
            historical=True,
        )
    except Exception:  # noqa: BLE001
        return False
    buy_trades = [row for row in (payload.get("trades") or []) if str(row.get("direction") or "").lower() == "buy"]
    if not buy_trades:
        return False
    total_amount = Decimal("0")
    weighted = Decimal("0")
    for trade in buy_trades:
        amount = to_decimal(trade.get("amount"))
        price = to_decimal(trade.get("price"))
        if amount <= 0 or price <= 0:
            continue
        total_amount += amount
        weighted += amount * price
    if total_amount <= 0 or total_amount < group.quantity * Decimal("0.5"):
        return False
    close_premium = weighted / total_amount
    index_price = (
        group.close_index_usd if group.close_index_usd and group.close_index_usd > 0 else group.entry_index_usd
    )
    if index_price <= 0:
        return False
    parsed = parse_option_name(group.short_instrument_name)
    base_currency = (parsed or {}).get("base_currency") or group.currency
    quote_currency = (parsed or {}).get("quote_currency") or "USDC"
    settlement_currency = quote_currency
    close_fee = option_trade_fee_usdc(
        index_price=index_price,
        premium=close_premium,
        quantity=group.quantity,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
    )
    gross = premium_value_usdc(
        index_price=index_price,
        premium=close_premium,
        quantity=group.quantity,
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
    )
    close_debit = gross + close_fee
    realized_pnl = group.entry_credit_net_usdc() - close_debit
    before_pnl = group.realized_pnl
    before_debit = group.realized_close_debit
    group.short_close_average_price = close_premium
    group.realized_close_debit = close_debit
    group.realized_close_fee = close_fee
    group.realized_pnl = realized_pnl
    group.realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss) if group.max_loss > 0 else None
    group.profit_capture = safe_div(max(group.entry_credit - close_debit, Decimal("0")), group.entry_credit)
    group.backfill_realized_pnl_usdc()
    before_apr = group.realized_apr_on_equity
    from .trade_apr import realized_apr_from_close

    apr = realized_apr_from_close(
        strategy=group.strategy or "naked_short",
        collateral_currency=group.collateral_currency or group.currency or "USDC",
        option_type=group.option_type or "put",
        quantity=group.quantity,
        contract_size=Decimal("1"),
        strike=group.short_strike,
        index_price_usd=index_price,
        estimated_im_collateral=group.estimated_im_collateral,
        covered_underlying_quantity=group.covered_underlying_quantity,
        pnl_collateral_native=realized_pnl
        if group.collateral_currency == "USDC"
        else (group.realized_pnl_collateral_native or Decimal("0")),
        entry_timestamp_ms=int(group.entry_timestamp_ms or 0),
        closed_timestamp_ms=closed_ms,
    )
    group.realized_apr_on_equity = apr
    group.realized_annualized_return = apr
    return (
        group.realized_pnl != before_pnl
        or group.realized_close_debit != before_debit
        or group.short_close_average_price != close_premium
        or group.realized_apr_on_equity != before_apr
    )


def repair_reconciled_bot_income_exit_in_state(
    state_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Repair closed groups mis-tagged reconciled_external after bot take-profit fills."""
    store = StrategyStateStore(state_path)
    state = store.load()
    cfg = _config_for_state(state_path)
    order_label_prefix = cfg.order_label_prefix if cfg is not None else "covered_call"
    profit_sweep_enabled = bool(cfg and cfg.covered_call_profit_sweep_enabled)
    journal_path = journal_db_path_for_state(state_path)
    journal = TradeJournalStore(journal_path) if journal_path.is_file() else None
    scope = scope_key_for_state(state_path)
    repaired = 0
    changed = False
    for group in state.groups:
        if group.status != "closed":
            continue
        executions: list[dict[str, Any]] = []
        if journal is not None and group.group_id:
            executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
        if repair_reconciled_bot_income_exit_group(
            group,
            executions,
            order_label_prefix=order_label_prefix,
            profit_sweep_enabled=profit_sweep_enabled,
        ):
            repaired += 1
            changed = True
    if changed and not dry_run:
        store.save(state)
        LOGGER.info(
            "repaired reconciled bot income exits in %s: count=%s",
            state_path.name,
            repaired,
        )
    return repaired


def _prepare_group_for_apr_backfill(
    group: TradeGroup,
    *,
    journal: TradeJournalStore | None,
    scope: str,
) -> None:
    if _is_covered_call_group(group) and group.covered_underlying_quantity <= 0 and group.quantity > 0:
        group.covered_underlying_quantity = group.quantity
    if not group.strategy and _is_covered_call_group(group):
        group.strategy = "covered_call"

    executions: list[dict[str, Any]] = []
    if journal is not None:
        executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
    if executions:
        group.enrich_fill_prices_from_journal(executions)
    group.infer_indices_from_fill_prices()
    if group.short_entry_average_price <= 0 and journal is not None and group.short_instrument_name:
        # Legacy synthetic rows stored USDC/qty instead of coin premium; borrow a plausible
        # open fill for the same instrument from another group in this journal.
        target = group.short_instrument_name
        peer_open: tuple[int, Decimal] | None = None
        for row in journal.list_executions(scope, limit=500):
            if str(row.get("instrument_name") or "") != target:
                continue
            if str(row.get("event_type") or "").lower() != "open":
                continue
            price = to_decimal(row.get("price"))
            if not group._premium_price_plausible(price):
                continue
            rank = group._journal_row_priority(row)
            if peer_open is None or rank < peer_open[0]:
                peer_open = (rank, price)
        if peer_open is not None:
            group.short_entry_average_price = peer_open[1]
            group.infer_indices_from_fill_prices()
    if group.short_entry_average_price <= 0:
        idx = group.entry_index_usd
        if idx <= 0 and group.close_index_usd is not None and group.close_index_usd > 0:
            idx = group.close_index_usd
        if idx > 0:
            group.enrich_fill_prices_from_ledger_spot(idx)


def _backfill_entry_net_apr_for_group(
    group: TradeGroup,
    *,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
) -> bool:
    """Recompute entry_net_apr from actual open credit, fee, and position size."""
    _ = fee_rate, fee_cap_rate  # screening-only; ledger APR ignores estimated fees

    if group.entry_timestamp_ms <= 0 or group.expiration_timestamp_ms <= group.entry_timestamp_ms:
        return False

    new_apr = group.entry_net_apr_at_open(contract_size=_contract_size_for_group(group))
    if new_apr <= 0:
        return False

    if group.short_entry_average_price <= 0:
        premium = group.resolved_short_entry_price()
        if premium > 0:
            group.short_entry_average_price = premium

    if new_apr == group.entry_net_apr:
        return False
    group.entry_net_apr = new_apr
    return True


def _contract_size_for_group(group: TradeGroup) -> Decimal:
    name = (group.short_instrument_name or "").upper()
    if "_USDC-" in name:
        return Decimal("0.1")
    return Decimal("1")


def _backfill_apr_for_group(group: TradeGroup) -> bool:
    """Recompute position-size realized APR for one closed group."""
    from .trade_apr import realized_apr_from_close

    if group.closed_timestamp_ms is None or group.entry_timestamp_ms <= 0:
        return False

    book = group.collateral_book()
    idx = (
        group.close_index_usd
        if group.close_index_usd is not None and group.close_index_usd > 0
        else group.entry_index_usd
    )
    if group.collateral_book() == "USDC":
        idx = group.underlying_index_usd_for_apr()

    pnl_native = group.realized_pnl_collateral_native
    if pnl_native is None and book == "USDC":
        pnl_native = group.realized_pnl
    elif (pnl_native is None or pnl_native == 0) and idx is not None and idx > 0 and group.realized_pnl is not None:
        pnl_native = group.realized_pnl / idx
    if pnl_native is None:
        return False

    qty = group.quantity if group.quantity > 0 else Decimal("1")
    if group.strategy == "covered_call" and group.covered_underlying_quantity <= 0:
        group.covered_underlying_quantity = qty

    apr = realized_apr_from_close(
        strategy=group.strategy or "naked_short",
        collateral_currency=book,
        option_type=group.option_type or "put",
        quantity=qty,
        contract_size=_contract_size_for_group(group),
        strike=group.short_strike,
        index_price_usd=idx if idx is not None else Decimal("0"),
        estimated_im_collateral=group.estimated_im_collateral,
        covered_underlying_quantity=group.covered_underlying_quantity,
        pnl_collateral_native=pnl_native,
        entry_timestamp_ms=group.entry_timestamp_ms,
        closed_timestamp_ms=group.closed_timestamp_ms,
    )
    group.realized_apr_on_equity = apr
    group.realized_annualized_return = apr
    return True


def _weighted_buy_premium_native(
    trades: list[dict[str, Any]],
    *,
    min_amount: Decimal,
) -> Decimal | None:
    total_amount = Decimal("0")
    weighted = Decimal("0")
    for trade in trades:
        if str(trade.get("direction") or "").lower() != "buy":
            continue
        amount = to_decimal(trade.get("amount"))
        price = to_decimal(trade.get("price"))
        if amount <= 0 or price <= 0:
            continue
        weighted += price * amount
        total_amount += amount
    if total_amount <= 0 or total_amount < min_amount:
        return None
    return weighted / total_amount


def _fetch_buy_close_trades_for_group(
    client: DeribitClient,
    group: TradeGroup,
) -> list[dict[str, Any]]:
    if not group.short_instrument_name or group.closed_timestamp_ms is None:
        return []
    start_ms = max(int(group.entry_timestamp_ms or 0) - 60_000, 0)
    end_ms = int(group.closed_timestamp_ms) + 300_000
    payload = client.get_user_trades_by_instrument(
        group.short_instrument_name,
        start_timestamp=start_ms,
        end_timestamp=end_ms,
        count=200,
        sorting="asc",
        historical=True,
    )
    return [row for row in (payload.get("trades") or []) if str(row.get("direction") or "").lower() == "buy"]


def _best_journal_close_premium_native(
    group: TradeGroup,
    executions: list[dict[str, Any]],
) -> Decimal | None:
    target = group.short_instrument_name
    if not target:
        return None
    best: tuple[int, Decimal] | None = None
    for row in executions:
        if str(row.get("instrument_name") or "") != target:
            continue
        if str(row.get("event_type") or "").lower() != "close":
            continue
        leg = str(row.get("leg") or "short")
        if leg not in {"", "short"}:
            continue
        price = to_decimal(row.get("price"))
        if not group._premium_price_plausible(price):
            continue
        rank = TradeGroup._journal_row_priority(row)
        if best is None or rank < best[0]:
            best = (rank, price)
    return best[1] if best is not None else None


def _coin_close_needs_repair(group: TradeGroup, authoritative: Decimal) -> bool:
    current = group.short_close_average_price
    if current is None or current <= 0:
        return True
    if authoritative <= 0:
        return False
    if group.close_reason == "reconciled_external" and current != authoritative:
        return True
    diff = abs(current - authoritative)
    tolerance = max(Decimal("0.00001"), authoritative * Decimal("0.002"))
    return diff >= tolerance


def _insert_instrument_buy_trades(
    store: TradeJournalStore,
    *,
    scope_key: str,
    client: DeribitClient,
    group: TradeGroup,
    strategy: str,
) -> int:
    if group.status != "closed" or not group.short_instrument_name:
        return 0
    try:
        buy_trades = _fetch_buy_close_trades_for_group(client, group)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(
            "instrument buy-trade lookup failed group=%s: %s",
            group.group_id,
            exc,
        )
        return 0
    inserted = 0
    for trade in buy_trades:
        instrument = str(trade.get("instrument_name") or "")
        amount = to_decimal(trade.get("amount"))
        price = to_decimal(trade.get("price"))
        if not instrument or amount <= 0 or price <= 0:
            continue
        ts_raw = trade.get("timestamp")
        ts_ms = int(ts_raw) if ts_raw is not None else int(group.closed_timestamp_ms or 0)
        if store.record_fill(
            scope_key=scope_key,
            event_type="close",
            source_action="backfill_api_instrument",
            instrument_name=instrument,
            direction="buy",
            amount=amount,
            price=price,
            group_id=group.group_id,
            leg="short",
            fee_usdc=_fee_usdc_from_trade(trade),
            order_id=str(trade.get("order_id") or ""),
            trade_id=_trade_id(trade),
            label=str(trade.get("label") or trade.get("order_label") or ""),
            strategy=strategy,
            reason=group.close_reason or "deribit_user_trades",
            ts_ms=ts_ms,
            extra={"source": "deribit_api", "backfill": "instrument_lookup"},
        ):
            inserted += 1
    return inserted


def repair_coin_close_prices_in_state(
    state_path: Path,
    *,
    client: DeribitClient | None = None,
    journal_store: TradeJournalStore | None = None,
    scope_key: str | None = None,
    strategy: str = "",
    dry_run: bool = False,
) -> int:
    """Fix coin-native close prices for closed groups (API fills > journal > skip)."""
    store = StrategyStateStore(state_path)
    state = store.load()
    scope = scope_key or scope_key_for_state(state_path)
    journal = journal_store
    if journal is None:
        journal_path = journal_db_path_for_state(state_path)
        journal = TradeJournalStore(journal_path) if journal_path.is_file() else None

    repaired = 0
    changed = False
    for group in state.groups:
        if group.status != "closed" or not group.is_coin_collateral():
            continue
        if not group.short_instrument_name or group.quantity <= 0:
            continue

        authoritative: Decimal | None = None
        if client is not None and journal is not None:
            _insert_instrument_buy_trades(
                journal,
                scope_key=scope,
                client=client,
                group=group,
                strategy=strategy or group.strategy or "",
            )
        if client is not None:
            try:
                buy_trades = _fetch_buy_close_trades_for_group(client, group)
                authoritative = _weighted_buy_premium_native(
                    buy_trades,
                    min_amount=group.quantity * Decimal("0.5"),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(
                    "repair close premium lookup failed group=%s: %s",
                    group.group_id,
                    exc,
                )

        if authoritative is None and journal is not None:
            executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
            authoritative = _best_journal_close_premium_native(group, executions)

        if authoritative is None or authoritative <= 0:
            continue
        if not _coin_close_needs_repair(group, authoritative):
            continue

        idx = group.close_index_usd or group.entry_index_usd
        if idx is None or idx <= 0:
            group.infer_indices_from_fill_prices()
            idx = group.close_index_usd or group.entry_index_usd
        if idx is None or idx <= 0:
            LOGGER.warning(
                "%s group=%s: skip coin close repair (missing index)",
                state_path.name,
                group.group_id,
            )
            continue

        before = group.short_close_average_price
        if group.apply_coin_close_from_native(
            short_close_premium=authoritative,
            index_usd=idx,
            close_fee_collateral=group.resolved_close_fee_collateral(),
        ):
            changed = True
            repaired += 1
            LOGGER.info(
                "%s group=%s: repaired coin close %s -> %s",
                state_path.name,
                group.group_id,
                before,
                authoritative,
            )

    if changed and not dry_run:
        store.save(state)
        try:
            from .frontend_server import invalidate_closed_groups_payload_cache

            invalidate_closed_groups_payload_cache()
        except Exception:
            pass
    return repaired


def backfill_closed_group_stats_in_state(
    state_path: Path,
    *,
    spot_index_usd: Decimal | None = None,
    spot_by_book: dict[str, Decimal] | None = None,
    dry_run: bool = False,
    fee_rate: Decimal | None = None,
    fee_cap_rate: Decimal | None = None,
) -> StateGroupStatsBackfillSummary:
    """Recompute entry_net_apr (all groups) + coin PnL / close APR (closed groups)."""
    store = StrategyStateStore(state_path)
    state = store.load()
    journal_path = journal_db_path_for_state(state_path)
    journal = TradeJournalStore(journal_path) if journal_path.is_file() else None
    scope = scope_key_for_state(state_path)

    if fee_rate is None or fee_cap_rate is None:
        resolved_fee_rate, resolved_fee_cap, _resolved_discount = _fee_rates_for_state(state_path)
        fee_rate = fee_rate if fee_rate is not None else resolved_fee_rate
        fee_cap_rate = fee_cap_rate if fee_cap_rate is not None else resolved_fee_cap

    closed_groups = 0
    pnl_updated = 0
    apr_updated = 0
    entry_apr_updated = 0
    changed = False

    for group in state.groups:
        if group.is_coin_collateral() and group.backfill_coin_collateral_ledger():
            changed = True

        if group.group_id and group.entry_timestamp_ms > 0:
            _prepare_group_for_apr_backfill(group, journal=journal, scope=scope)
            before_entry_apr = group.entry_net_apr
            if _backfill_entry_net_apr_for_group(
                group,
                fee_rate=fee_rate,
                fee_cap_rate=fee_cap_rate,
            ):
                if group.entry_net_apr != before_entry_apr:
                    entry_apr_updated += 1
                    changed = True
                    if journal is not None:
                        stats = journal.get_group_stats(scope, group.group_id)
                        if stats is not None:
                            journal.record_group_stats_open(
                                scope_key=scope,
                                group_id=group.group_id,
                                collateral_book=stats.get("collateral_book") or group.collateral_book(),
                                opened_ts_ms=int(stats.get("opened_ts_ms") or group.entry_timestamp_ms),
                                entry_book_equity=to_decimal(stats.get("entry_book_equity")),
                                entry_net_apr=group.entry_net_apr,
                                entry_credit_usdc=to_decimal(stats.get("entry_credit_usdc") or group.entry_credit),
                            )

        if group.status != "closed" or group.closed_timestamp_ms is None:
            continue
        if group.entry_timestamp_ms <= 0:
            continue
        closed_groups += 1

        if journal is not None:
            executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
            restore_long_leg_from_journal_executions(group, executions)
        idx = settlement_index_usd_for_group(group, spot_by_book=spot_by_book)
        if idx is not None:
            before_spread_pnl = group.realized_pnl
            if (
                repair_bull_put_expiry_reconcile_pnl(
                    group,
                    index_price_usd=idx,
                    fee_rate=fee_rate,
                    fee_cap_rate=fee_cap_rate,
                    markets={},
                )
                and group.realized_pnl != before_spread_pnl
            ):
                pnl_updated += 1
                changed = True

        if journal is not None and group.group_id:
            executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
            cfg = _config_for_state(state_path)
            if cfg is not None and repair_reconciled_bot_income_exit_group(
                group,
                executions,
                order_label_prefix=cfg.order_label_prefix,
                profit_sweep_enabled=cfg.covered_call_profit_sweep_enabled,
            ):
                changed = True

        if group.is_coin_collateral():
            executions: list[dict[str, Any]] = []
            if journal is not None:
                executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
            book = group.collateral_book()
            spot = None
            if spot_by_book:
                spot = spot_by_book.get(book)
            if spot is None and spot_index_usd is not None:
                spot = spot_index_usd
            before_native = group.realized_pnl_collateral_native
            before_entry_idx = group.entry_index_usd
            before_close_idx = group.close_index_usd
            group.backfill_realized_pnl_collateral_native(
                spot_index_usd=spot,
                journal_executions=executions,
            )
            group.infer_indices_from_fill_prices()
            if group.realized_pnl_collateral_native is not None and (
                group.realized_pnl_collateral_native != before_native
                or group.entry_index_usd != before_entry_idx
                or group.close_index_usd != before_close_idx
            ):
                pnl_updated += 1
                changed = True

        before_apr = group.realized_apr_on_equity
        if _backfill_apr_for_group(group):
            if group.realized_apr_on_equity != before_apr:
                apr_updated += 1
                changed = True

    saved = False
    if changed and not dry_run:
        store.save(state)
        saved = True
        LOGGER.info(
            "%s backfilled closed stats: pnl=%s apr=%s (closed=%s)",
            state_path.name,
            pnl_updated,
            apr_updated,
            closed_groups,
        )
        try:
            from .frontend_server import invalidate_closed_groups_payload_cache

            invalidate_closed_groups_payload_cache()
        except Exception:
            pass

    return StateGroupStatsBackfillSummary(
        state_file=str(state_path),
        closed_groups=closed_groups,
        pnl_updated=pnl_updated,
        apr_updated=apr_updated,
        entry_apr_updated=entry_apr_updated,
        saved=saved,
    )


def recalculate_closed_coin_pnl_in_state(
    state_path: Path,
    *,
    spot_index_usd: Decimal | None = None,
    spot_by_book: dict[str, Decimal] | None = None,
) -> int:
    """Recompute coin realized PnL for all closed groups and persist to state."""
    summary = backfill_closed_group_stats_in_state(
        state_path,
        spot_index_usd=spot_index_usd,
        spot_by_book=spot_by_book,
        dry_run=False,
    )
    return summary.pnl_updated


def _backfill_group_from_state(
    store: TradeJournalStore,
    *,
    scope_key: str,
    group: TradeGroup,
    skip_if_journal_exists: bool,
) -> int:
    if skip_if_journal_exists and _group_has_journal(store, scope_key, group.group_id):
        return 0

    inserted = 0
    strategy = group.strategy or ""
    qty = group.quantity
    if qty <= 0:
        return 0

    if group.entry_timestamp_ms and group.entry_timestamp_ms > 0 and group.short_instrument_name:
        gross = group.entry_credit + group.entry_fee
        entry_price = _state_synthetic_premium_price(gross, qty, group)
        label = group.short_label or ""
        if entry_price is None:
            pass
        elif store.record_fill(
            scope_key=scope_key,
            event_type="open",
            source_action="backfill_state",
            instrument_name=group.short_instrument_name,
            direction="sell",
            amount=qty,
            price=entry_price,
            group_id=group.group_id,
            leg="short",
            fee_usdc=group.entry_fee if group.entry_fee > 0 else None,
            label=label,
            strategy=strategy,
            reason=group.last_action or "entry",
            ts_ms=int(group.entry_timestamp_ms),
            trade_id=_synthetic_trade_id(group.group_id, "open", "short", group.short_instrument_name),
            extra={"synthetic": True, "source": "strategy_state"},
        ):
            inserted += 1

    if group.long_instrument_name and group.entry_timestamp_ms and group.entry_timestamp_ms > 0:
        # Long leg premium not stored separately; record placeholder at entry time.
        if store.record_fill(
            scope_key=scope_key,
            event_type="open",
            source_action="backfill_state",
            instrument_name=group.long_instrument_name,
            direction="buy",
            amount=qty,
            price=Decimal("0"),
            group_id=group.group_id,
            leg="long",
            label=group.long_label or "",
            strategy=strategy,
            reason="long_leg_entry_unknown_price",
            ts_ms=int(group.entry_timestamp_ms),
            trade_id=_synthetic_trade_id(group.group_id, "open", "long", group.long_instrument_name),
            extra={"synthetic": True, "source": "strategy_state", "price_unknown": True},
        ):
            inserted += 1

    if group.status == "closed" and group.closed_timestamp_ms and group.short_instrument_name:
        close_fee = group.realized_close_fee
        if close_fee is None:
            close_fee = group.current_close_fee
        close_price = None
        if group.short_close_average_price is not None and group.short_close_average_price > 0:
            close_price = group.short_close_average_price
        else:
            close_debit = group.realized_close_debit
            if close_debit is None:
                close_debit = group.current_debit
            premium_usdc = max((close_debit or Decimal("0")) - (close_fee or Decimal("0")), Decimal("0"))
            if not group.is_coin_collateral():
                close_price = _state_synthetic_premium_price(premium_usdc, qty, group)
        close_label = f"{group.short_label}-close" if group.short_label else ""
        if close_price is None:
            pass
        elif store.record_fill(
            scope_key=scope_key,
            event_type="close",
            source_action="backfill_state",
            instrument_name=group.short_instrument_name,
            direction="buy",
            amount=qty,
            price=close_price,
            group_id=group.group_id,
            leg="short",
            fee_usdc=close_fee if close_fee and close_fee > 0 else None,
            label=close_label,
            strategy=strategy,
            reason=group.close_reason or group.last_action or "close",
            ts_ms=int(group.closed_timestamp_ms),
            trade_id=_synthetic_trade_id(group.group_id, "close", "short", group.short_instrument_name),
            extra={
                "synthetic": True,
                "source": "strategy_state",
                "realized_pnl_usdc": str(group.realized_pnl) if group.realized_pnl is not None else None,
            },
        ):
            inserted += 1

    return inserted


def iter_api_option_trades(
    client: DeribitClient,
    currencies: tuple[str, ...],
    *,
    historical: bool = True,
    start_timestamp_ms: int | None = None,
) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    for currency in currencies:
        cursor_ts = int(start_timestamp_ms or 0)
        while True:
            batch = client.get_user_trades_by_currency(
                currency,
                kind="option",
                count=1000,
                sorting="asc",
                historical=historical,
                start_timestamp=cursor_ts if cursor_ts > 0 else None,
            )
            trades = list(batch.get("trades") or [])
            if not trades:
                break
            for trade in trades:
                tid = _trade_id(trade)
                if tid:
                    if tid in seen:
                        continue
                    seen.add(tid)
                yield trade
            if not batch.get("has_more"):
                break
            last_ts = int(trades[-1].get("timestamp") or 0)
            if last_ts <= 0 or last_ts < cursor_ts:
                break
            cursor_ts = last_ts + 1


def _backfill_from_api(
    store: TradeJournalStore,
    *,
    scope_key: str,
    client: DeribitClient,
    config: BotConfig,
    currencies: tuple[str, ...],
    historical: bool,
    start_timestamp_ms: int | None,
) -> tuple[int, int, int]:
    prefix = config.order_label_prefix
    seen = 0
    inserted = 0
    skipped_label = 0
    for trade in iter_api_option_trades(
        client,
        currencies,
        historical=historical,
        start_timestamp_ms=start_timestamp_ms,
    ):
        seen += 1
        label = str(trade.get("label") or trade.get("order_label") or "")
        parsed = _parse_bot_label(label, label_prefix=prefix)
        if parsed is None:
            skipped_label += 1
            continue
        group_id, leg, event_type = parsed
        instrument = str(trade.get("instrument_name") or "")
        amount = to_decimal(trade.get("amount"))
        if not instrument or amount <= 0:
            skipped_label += 1
            continue
        direction = str(trade.get("direction") or "").lower()
        if not direction:
            direction = "sell" if event_type == "open" else "buy"
        ts_raw = trade.get("timestamp")
        ts_ms = int(ts_raw) if ts_raw is not None else utc_now_ms()
        if store.record_fill(
            scope_key=scope_key,
            event_type=event_type,
            source_action="backfill_api",
            instrument_name=instrument,
            direction=direction,
            amount=amount,
            price=to_decimal(trade.get("price")),
            group_id=group_id,
            leg=leg,
            fee_usdc=_fee_usdc_from_trade(trade),
            order_id=str(trade.get("order_id") or ""),
            trade_id=_trade_id(trade),
            label=label,
            strategy=config.option_strategy,
            reason="deribit_user_trades",
            ts_ms=ts_ms,
            extra={"source": "deribit_api"},
        ):
            inserted += 1
    return seen, inserted, skipped_label


def iter_api_hedge_perp_trades(
    client: DeribitClient,
    currencies: tuple[str, ...],
    *,
    historical: bool = True,
    start_timestamp_ms: int | None = None,
) -> Iterator[dict[str, Any]]:
    from .hedge_pnl import is_hedge_perp_instrument, is_hedge_perp_label

    managed = {str(c).upper() for c in currencies}
    seen: set[str] = set()
    cursor_ts = int(start_timestamp_ms or 0)
    while True:
        batch = client.get_user_trades_by_currency(
            "USDC",
            kind="future",
            count=1000,
            sorting="asc",
            historical=historical,
            start_timestamp=cursor_ts if cursor_ts > 0 else None,
        )
        trades = list(batch.get("trades") or [])
        if not trades:
            break
        for trade in trades:
            instrument = str(trade.get("instrument_name") or "")
            label = str(trade.get("label") or trade.get("order_label") or "")
            if not is_hedge_perp_instrument(instrument) or not is_hedge_perp_label(label):
                continue
            base = instrument.split("_")[0].upper() if "_" in instrument else ""
            if managed and base not in managed:
                continue
            tid = _trade_id(trade)
            if tid:
                if tid in seen:
                    continue
                seen.add(tid)
            yield trade
        if not batch.get("has_more"):
            break
        last_ts = int(trades[-1].get("timestamp") or 0)
        if last_ts <= 0 or last_ts < cursor_ts:
            break
        cursor_ts = last_ts + 1


def _backfill_hedge_perp_from_api(
    store: TradeJournalStore,
    *,
    scope_key: str,
    client: DeribitClient,
    config: BotConfig,
    currencies: tuple[str, ...],
    historical: bool,
    start_timestamp_ms: int | None,
) -> tuple[int, int]:
    seen = 0
    inserted = 0
    for trade in iter_api_hedge_perp_trades(
        client,
        currencies,
        historical=historical,
        start_timestamp_ms=start_timestamp_ms,
    ):
        seen += 1
        instrument = str(trade.get("instrument_name") or "")
        amount = to_decimal(trade.get("amount"))
        if not instrument or amount <= 0:
            continue
        direction = str(trade.get("direction") or "").lower()
        if not direction:
            continue
        ts_raw = trade.get("timestamp")
        ts_ms = int(ts_raw) if ts_raw is not None else utc_now_ms()
        pl_raw = trade.get("profit_loss")
        extra: dict[str, Any] = {"source": "deribit_api", "hedge": True}
        if pl_raw is not None and str(pl_raw).strip() != "":
            extra["profit_loss"] = str(pl_raw)
        if store.record_fill(
            scope_key=scope_key,
            event_type="hedge",
            source_action="backfill_api_hedge",
            instrument_name=instrument,
            direction=direction,
            amount=amount,
            price=to_decimal(trade.get("price")),
            fee_usdc=_fee_usdc_from_trade(trade),
            order_id=str(trade.get("order_id") or ""),
            trade_id=_trade_id(trade),
            label=str(trade.get("label") or trade.get("order_label") or ""),
            strategy=config.option_strategy,
            reason="deribit_hedge_perp",
            ts_ms=ts_ms,
            extra=extra,
        ):
            inserted += 1
    return seen, inserted


def _sync_metrics_from_state(
    state_path: Path,
    closed_payloads: list[dict[str, Any]],
    *,
    metrics_db: Path,
) -> None:
    scope_key = performance_scope_key([_ScopeAccount(state_path.stem, state_path)])
    fingerprint = _state_fingerprint(state_path, len(closed_payloads))
    metrics = MetricsStore(metrics_db)
    if metrics.is_synced(scope_key, fingerprint):
        return
    metrics.sync_from_closed(
        scope_key,
        fingerprint,
        closed_payloads,
        synced_at_ms=utc_now_ms(),
    )


def backfill_account(
    env_file: Path,
    *,
    use_api: bool = True,
    use_state: bool = True,
    sync_metrics: bool = True,
    metrics_db: Path | None = None,
    historical: bool = True,
    start_timestamp_ms: int | None = None,
    skip_state_if_group_has_journal: bool = True,
) -> BackfillSummary:
    cfg = load_config(env_file, require_private=False)
    state_path = cfg.state_file.resolve()
    journal_path = journal_db_path_for_state(state_path)
    store = TradeJournalStore(journal_path)
    scope_key = scope_key_for_state(state_path)

    groups = load_trade_groups(state_path)
    closed_payloads = [g.to_dict() for g in groups if g.status == "closed"]

    api_seen = api_inserted = api_skipped = 0
    client: DeribitClient | None = None
    if use_api and cfg.has_private_credentials:
        client = DeribitClient(cfg)
        currencies = tuple(dict.fromkeys(list(cfg.managed_currencies) + ["USDC"]))
        api_seen, api_inserted, api_skipped = _backfill_from_api(
            store,
            scope_key=scope_key,
            client=client,
            config=cfg,
            currencies=currencies,
            historical=historical,
            start_timestamp_ms=start_timestamp_ms,
        )
        LOGGER.info(
            "%s API backfill: seen=%s inserted=%s skipped_label=%s",
            state_path.name,
            api_seen,
            api_inserted,
            api_skipped,
        )
        hedge_seen, hedge_inserted = _backfill_hedge_perp_from_api(
            store,
            scope_key=scope_key,
            client=client,
            config=cfg,
            currencies=tuple(cfg.managed_currencies),
            historical=historical,
            start_timestamp_ms=start_timestamp_ms,
        )
        LOGGER.info(
            "%s hedge perp API backfill: seen=%s inserted=%s",
            state_path.name,
            hedge_seen,
            hedge_inserted,
        )

    coin_close_repaired = repair_coin_close_prices_in_state(
        state_path,
        client=client,
        journal_store=store,
        scope_key=scope_key,
        strategy=cfg.option_strategy,
    )
    if coin_close_repaired:
        LOGGER.info("%s coin close repair: groups=%s", state_path.name, coin_close_repaired)

    state_inserted = 0
    state_skipped = 0
    if use_state:
        for group in groups:
            n = _backfill_group_from_state(
                store,
                scope_key=scope_key,
                group=group,
                skip_if_journal_exists=skip_state_if_group_has_journal,
            )
            if n == 0 and skip_state_if_group_has_journal and _group_has_journal(store, scope_key, group.group_id):
                state_skipped += 1
            state_inserted += n
        LOGGER.info(
            "%s state backfill: groups=%s inserted_rows=%s skipped_groups=%s",
            state_path.name,
            len(groups),
            state_inserted,
            state_skipped,
        )

    coin_recalculated = recalculate_closed_coin_pnl_in_state(state_path, spot_index_usd=None)

    metrics_ok = False
    if sync_metrics:
        groups_after = load_trade_groups(state_path)
        closed_payloads = [g.to_dict() for g in groups_after if g.status == "closed"]
        if closed_payloads:
            db_path = metrics_db or _default_metrics_db_for_env(env_file)
            _sync_metrics_from_state(state_path, closed_payloads, metrics_db=db_path)
            metrics_ok = True

    return BackfillSummary(
        env_file=str(env_file.resolve()),
        state_file=str(state_path),
        journal_db=str(journal_path),
        api_trades_seen=api_seen,
        api_inserted=api_inserted,
        api_skipped_label=api_skipped,
        state_groups=len(groups),
        state_groups_skipped=state_skipped,
        state_inserted=state_inserted,
        metrics_synced=metrics_ok,
        coin_groups_recalculated=coin_recalculated,
        coin_close_repaired=coin_close_repaired,
    )


def sync_incremental_journal(
    env_file: Path,
    *,
    overlap_ms: int = 3_600_000,
    historical: bool = True,
) -> dict[str, Any]:
    """Fetch only Deribit fills newer than the latest row already in the journal."""
    cfg = load_config(env_file, require_private=True)
    if not cfg.has_private_credentials:
        return {"skipped": True, "reason": "no_credentials"}
    state_path = cfg.state_file.resolve()
    store = TradeJournalStore(journal_db_path_for_state(state_path))
    scope_key = scope_key_for_state(state_path)
    last_ts = store.max_ts_ms(scope_key)
    start_timestamp_ms = max(0, last_ts - overlap_ms) if last_ts else None
    client = DeribitClient(cfg)
    currencies = tuple(dict.fromkeys(list(cfg.managed_currencies) + ["USDC"]))
    api_seen, api_inserted, api_skipped = _backfill_from_api(
        store,
        scope_key=scope_key,
        client=client,
        config=cfg,
        currencies=currencies,
        historical=historical,
        start_timestamp_ms=start_timestamp_ms,
    )
    hedge_seen, hedge_inserted = _backfill_hedge_perp_from_api(
        store,
        scope_key=scope_key,
        client=client,
        config=cfg,
        currencies=tuple(cfg.managed_currencies),
        historical=historical,
        start_timestamp_ms=start_timestamp_ms,
    )
    return {
        "env_file": str(env_file.resolve()),
        "state_file": str(state_path),
        "journal_db": str(journal_db_path_for_state(state_path)),
        "last_ts_ms": last_ts,
        "start_timestamp_ms": start_timestamp_ms,
        "api_trades_seen": api_seen,
        "api_inserted": api_inserted,
        "api_skipped_label": api_skipped,
        "hedge_trades_seen": hedge_seen,
        "hedge_inserted": hedge_inserted,
    }


def sync_incremental_investor(
    investor: str | Path,
    *,
    repo_root: Path | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    from .env_layout import find_repo_root, load_investor_manifest

    root = find_repo_root(repo_root or Path.cwd())
    manifest = load_investor_manifest(investor, repo_root=root)
    return [sync_incremental_journal(account.env_path, **kwargs) for account in manifest.operational_accounts()]


def backfill_investor(
    investor: str | Path,
    *,
    repo_root: Path | None = None,
    **kwargs: Any,
) -> list[BackfillSummary]:
    from .env_layout import find_repo_root, investor_metrics_db_path, load_investor_manifest

    root = find_repo_root(repo_root or Path.cwd())
    manifest = load_investor_manifest(investor, repo_root=root)
    if root is not None and kwargs.get("metrics_db") is None:
        kwargs = {**kwargs, "metrics_db": investor_metrics_db_path(root, manifest.investor_id)}
    summaries: list[BackfillSummary] = []
    for account in manifest.enabled_accounts():
        LOGGER.info("Backfilling %s (%s)", account.slug, account.env_path)
        summaries.append(backfill_account(account.env_path, **kwargs))
    return summaries


def backfill_all_investors(
    *,
    repo_root: Path | None = None,
    skip_investor_ids: frozenset[str] = frozenset({"_example"}),
    **kwargs: Any,
) -> list[BackfillSummary]:
    """Run ``backfill_investor`` for every investor under ``config/investors/``."""
    from .env_layout import find_repo_root
    from .investor_ops import list_investors

    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise ValueError("Cannot locate repository root")
    rows = list_investors(repo_root=root)
    summaries: list[BackfillSummary] = []
    for row in rows:
        investor_id = str(row.get("investor_id") or "")
        if not investor_id or investor_id in skip_investor_ids:
            continue
        try:
            summaries.extend(backfill_investor(investor_id, repo_root=root, **kwargs))
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("backfill failed for investor %s: %s", investor_id, exc)
    return summaries
