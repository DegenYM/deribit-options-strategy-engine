"""Backfill trade_journal SQLite from Deribit API fills and/or local strategy state."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
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
from .utils import safe_div, to_decimal, utc_now_ms

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
    if (group.strategy or "") == "covered_call":
        return True
    label = str(group.short_label or "")
    if label.startswith("covered_call-"):
        return True
    return group.option_type == "call" and group.covered_underlying_quantity > 0


def _fee_rates_for_state(state_path: Path) -> tuple[Decimal, Decimal]:
    """Best-effort fee rates from a sibling account env; else Deribit defaults."""
    try:
        from .config import load_config
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
        for env_path in candidates:
            if env_path.is_file():
                cfg = load_config(env_path, require_private=False)
                return cfg.option_fee_rate, cfg.option_fee_cap_rate
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_OPTION_FEE_RATE, _DEFAULT_OPTION_FEE_CAP_RATE


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
        resolved_fee_rate, resolved_fee_cap = _fee_rates_for_state(state_path)
        fee_rate = fee_rate if fee_rate is not None else resolved_fee_rate
        fee_cap_rate = fee_cap_rate if fee_cap_rate is not None else resolved_fee_cap

    closed_groups = 0
    pnl_updated = 0
    apr_updated = 0
    entry_apr_updated = 0
    changed = False

    for group in state.groups:
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
        close_debit = group.realized_close_debit
        if close_debit is None:
            close_debit = group.current_debit
        close_fee = group.realized_close_fee
        if close_fee is None:
            close_fee = group.current_close_fee
        premium_usdc = max((close_debit or Decimal("0")) - (close_fee or Decimal("0")), Decimal("0"))
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
    return {
        "env_file": str(env_file.resolve()),
        "state_file": str(state_path),
        "journal_db": str(journal_db_path_for_state(state_path)),
        "last_ts_ms": last_ts,
        "start_timestamp_ms": start_timestamp_ms,
        "api_trades_seen": api_seen,
        "api_inserted": api_inserted,
        "api_skipped_label": api_skipped,
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
