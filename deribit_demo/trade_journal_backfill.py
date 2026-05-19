"""Backfill trade_journal SQLite from Deribit API fills and/or local strategy state."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

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


def recalculate_closed_coin_pnl_in_state(
    state_path: Path,
    *,
    spot_index_usd: Decimal | None = None,
    spot_by_book: dict[str, Decimal] | None = None,
) -> int:
    """Recompute coin realized PnL for all closed groups and persist to state."""
    store = StrategyStateStore(state_path)
    state = store.load()
    journal_path = journal_db_path_for_state(state_path)
    journal = TradeJournalStore(journal_path) if journal_path.is_file() else None
    scope = scope_key_for_state(state_path)
    updated = 0
    for group in state.groups:
        if group.status != "closed" or not group.is_coin_collateral():
            continue
        executions: list[dict[str, Any]] = []
        if journal is not None:
            executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
        book = group.collateral_book()
        spot = None
        if spot_by_book:
            spot = spot_by_book.get(book)
        if spot is None and spot_index_usd is not None:
            spot = spot_index_usd
        group.backfill_realized_pnl_collateral_native(
            spot_index_usd=spot,
            journal_executions=executions,
        )
        if group.realized_pnl_collateral_native is None:
            continue
        updated += 1
    if updated:
        store.save(state)
        LOGGER.info("%s recalculated coin realized PnL for %s closed group(s)", state_path.name, updated)
        try:
            from .frontend_server import invalidate_closed_groups_payload_cache

            invalidate_closed_groups_payload_cache()
        except Exception:
            pass
    return updated


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
        from .state import load_trade_groups

        groups_after = load_trade_groups(state_path)
        closed_payloads = [g.to_dict() for g in groups_after if g.status == "closed"]
        if closed_payloads:
            db_path = metrics_db or (Path("data/frontend_ledger") / "metrics.db")
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
    return [sync_incremental_journal(account.env_path, **kwargs) for account in manifest.enabled_accounts()]


def backfill_investor(
    investor: str | Path,
    *,
    repo_root: Path | None = None,
    **kwargs: Any,
) -> list[BackfillSummary]:
    from .env_layout import find_repo_root, load_investor_manifest

    root = find_repo_root(repo_root or Path.cwd())
    manifest = load_investor_manifest(investor, repo_root=root)
    summaries: list[BackfillSummary] = []
    for account in manifest.enabled_accounts():
        LOGGER.info("Backfilling %s (%s)", account.slug, account.env_path)
        summaries.append(backfill_account(account.env_path, **kwargs))
    return summaries
