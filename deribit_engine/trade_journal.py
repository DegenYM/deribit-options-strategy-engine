"""Persistent log of Deribit API fills for bot open/close executions."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from .utils import json_default, safe_div, to_decimal, utc_now_ms

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source_action TEXT NOT NULL,
    group_id TEXT,
    leg TEXT,
    instrument_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount TEXT NOT NULL,
    price TEXT NOT NULL,
    fee_usdc TEXT,
    order_id TEXT,
    trade_id TEXT,
    label TEXT,
    strategy TEXT,
    reason TEXT,
    extra_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_executions_dedupe
    ON trade_executions (scope_key, trade_id)
    WHERE trade_id IS NOT NULL AND trade_id != '';

CREATE INDEX IF NOT EXISTS idx_trade_executions_scope_ts
    ON trade_executions (scope_key, ts_ms DESC);

CREATE INDEX IF NOT EXISTS idx_trade_executions_group
    ON trade_executions (scope_key, group_id);

CREATE TABLE IF NOT EXISTS trade_group_stats (
    scope_key TEXT NOT NULL,
    group_id TEXT NOT NULL,
    collateral_book TEXT NOT NULL,
    opened_ts_ms INTEGER,
    entry_book_equity TEXT,
    entry_net_apr TEXT,
    entry_credit_usdc TEXT,
    closed_ts_ms INTEGER,
    close_book_equity TEXT,
    realized_pnl_usdc TEXT,
    realized_apr_on_equity TEXT,
    holding_days TEXT,
    updated_ts_ms INTEGER NOT NULL,
    PRIMARY KEY (scope_key, group_id)
);
"""


def journal_db_path_for_state(state_file: Path) -> Path:
    return state_file.with_name(f"{state_file.stem}.trade_journal.db")


def scope_key_for_state(state_file: Path) -> str:
    return str(state_file.resolve())


def _trade_id(trade: dict[str, Any]) -> str:
    for key in ("trade_id", "trade_seq", "id"):
        raw = trade.get(key)
        if raw is not None and str(raw).strip():
            return str(raw)
    order_id = str(trade.get("order_id") or "")
    ts = trade.get("timestamp")
    instrument = str(trade.get("instrument_name") or "")
    amount = str(trade.get("amount") or "")
    if order_id and ts is not None:
        return f"{order_id}:{ts}:{instrument}:{amount}"
    return ""


def _fee_usdc_from_trade(trade: dict[str, Any]) -> Decimal | None:
    fee = to_decimal(trade.get("fee"))
    if fee <= 0:
        return None
    fee_ccy = str(trade.get("fee_currency") or "USDC").upper()
    if fee_ccy == "USDC":
        return fee
    idx = to_decimal(trade.get("index_price"))
    if idx > 0:
        return fee if fee_ccy == "USDC" else fee * idx
    return fee


class TradeJournalStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()

    def record_fill(
        self,
        *,
        scope_key: str,
        event_type: str,
        source_action: str,
        instrument_name: str,
        direction: str,
        amount: Decimal,
        price: Decimal,
        group_id: str = "",
        leg: str = "",
        fee_usdc: Decimal | None = None,
        order_id: str = "",
        trade_id: str = "",
        label: str = "",
        strategy: str = "",
        reason: str = "",
        ts_ms: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Insert one fill row. Returns False when deduped."""
        tid = (trade_id or "").strip()
        ts = int(ts_ms if ts_ms is not None else utc_now_ms())
        extra_json = json.dumps(extra or {}, default=json_default, ensure_ascii=False)
        row = (
            scope_key,
            ts,
            event_type,
            source_action,
            group_id or None,
            leg or None,
            instrument_name,
            direction.lower(),
            str(amount),
            str(price),
            str(fee_usdc) if fee_usdc is not None else None,
            order_id or None,
            tid or None,
            label or None,
            strategy or None,
            reason or None,
            extra_json,
        )
        with self._lock:
            with self._connect() as conn:
                if tid:
                    existing = conn.execute(
                        """
                        SELECT 1 FROM trade_executions
                        WHERE scope_key = ? AND trade_id = ?
                        """,
                        (scope_key, tid),
                    ).fetchone()
                    if existing:
                        return False
                conn.execute(
                    """
                    INSERT INTO trade_executions (
                        scope_key, ts_ms, event_type, source_action, group_id, leg,
                        instrument_name, direction, amount, price, fee_usdc,
                        order_id, trade_id, label, strategy, reason, extra_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                conn.commit()
        return True

    def record_fills(
        self,
        *,
        scope_key: str,
        event_type: str,
        source_action: str,
        trades: list[dict[str, Any]],
        group_id: str = "",
        leg: str = "",
        strategy: str = "",
        reason: str = "",
        direction_hint: str = "",
        extra: dict[str, Any] | None = None,
    ) -> int:
        inserted = 0
        for trade in trades:
            instrument = str(trade.get("instrument_name") or "")
            if not instrument:
                continue
            amount = to_decimal(trade.get("amount"))
            price = to_decimal(trade.get("price"))
            if amount <= 0:
                continue
            direction = str(trade.get("direction") or direction_hint or "").lower()
            if not direction:
                direction = "sell" if event_type == "open" else "buy"
            ts_raw = trade.get("timestamp")
            ts_ms = int(ts_raw) if ts_raw is not None else None
            trade_extra = dict(extra or {})
            pl_raw = trade.get("profit_loss")
            if pl_raw is not None and str(pl_raw).strip() != "":
                trade_extra["profit_loss"] = str(pl_raw)
            if self.record_fill(
                scope_key=scope_key,
                event_type=event_type,
                source_action=source_action,
                instrument_name=instrument,
                direction=direction,
                amount=amount,
                price=price,
                group_id=group_id,
                leg=leg,
                fee_usdc=_fee_usdc_from_trade(trade),
                order_id=str(trade.get("order_id") or ""),
                trade_id=_trade_id(trade),
                label=str(trade.get("label") or trade.get("order_label") or ""),
                strategy=strategy,
                reason=reason,
                ts_ms=ts_ms,
                extra=trade_extra,
            ):
                inserted += 1
        return inserted

    def max_ts_ms(self, scope_key: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(ts_ms) FROM trade_executions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def execution_count(self, scope_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trade_executions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_executions(
        self,
        scope_key: str,
        *,
        limit: int = 500,
        since_ms: int | None = None,
        group_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["scope_key = ?"]
        params: list[Any] = [scope_key]
        if since_ms is not None:
            clauses.append("ts_ms >= ?")
            params.append(int(since_ms))
        if group_id:
            clauses.append("group_id = ?")
            params.append(group_id)
        where = " AND ".join(clauses)
        params.append(max(1, min(int(limit), 5000)))
        sql = f"""
            SELECT ts_ms, event_type, source_action, group_id, leg, instrument_name,
                   direction, amount, price, fee_usdc, order_id, trade_id, label,
                   strategy, reason, extra_json
            FROM trade_executions
            WHERE {where}
            ORDER BY ts_ms DESC, id DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            extra_raw = row[15]
            try:
                extra = json.loads(extra_raw) if extra_raw else {}
            except json.JSONDecodeError:
                extra = {}
            out.append(
                {
                    "ts_ms": row[0],
                    "event_type": row[1],
                    "source_action": row[2],
                    "group_id": row[3],
                    "leg": row[4],
                    "instrument_name": row[5],
                    "direction": row[6],
                    "amount": row[7],
                    "price": row[8],
                    "fee_usdc": row[9],
                    "order_id": row[10],
                    "trade_id": row[11],
                    "label": row[12],
                    "strategy": row[13],
                    "reason": row[14],
                    "extra": extra,
                }
            )
        return out

    def record_group_stats_open(
        self,
        *,
        scope_key: str,
        group_id: str,
        collateral_book: str,
        opened_ts_ms: int,
        entry_book_equity: Decimal,
        entry_net_apr: Decimal,
        entry_credit_usdc: Decimal,
    ) -> None:
        book = str(collateral_book or "USDC").upper()
        now = utc_now_ms()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO trade_group_stats (
                        scope_key, group_id, collateral_book,
                        opened_ts_ms, entry_book_equity, entry_net_apr, entry_credit_usdc,
                        updated_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_key, group_id) DO UPDATE SET
                        collateral_book = excluded.collateral_book,
                        opened_ts_ms = excluded.opened_ts_ms,
                        entry_book_equity = excluded.entry_book_equity,
                        entry_net_apr = excluded.entry_net_apr,
                        entry_credit_usdc = excluded.entry_credit_usdc,
                        updated_ts_ms = excluded.updated_ts_ms
                    """,
                    (
                        scope_key,
                        group_id,
                        book,
                        int(opened_ts_ms),
                        str(entry_book_equity),
                        str(entry_net_apr),
                        str(entry_credit_usdc),
                        now,
                    ),
                )
                conn.commit()

    def record_group_stats_close(
        self,
        *,
        scope_key: str,
        group_id: str,
        collateral_book: str,
        closed_ts_ms: int,
        close_book_equity: Decimal,
        realized_pnl_usdc: Decimal,
        realized_apr_on_equity: Decimal,
        holding_days: Decimal,
    ) -> None:
        book = str(collateral_book or "USDC").upper()
        now = utc_now_ms()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE trade_group_stats SET
                        collateral_book = ?,
                        closed_ts_ms = ?,
                        close_book_equity = ?,
                        realized_pnl_usdc = ?,
                        realized_apr_on_equity = ?,
                        holding_days = ?,
                        updated_ts_ms = ?
                    WHERE scope_key = ? AND group_id = ?
                    """,
                    (
                        book,
                        int(closed_ts_ms),
                        str(close_book_equity),
                        str(realized_pnl_usdc),
                        str(realized_apr_on_equity),
                        str(holding_days),
                        now,
                        scope_key,
                        group_id,
                    ),
                )
                if conn.total_changes == 0:
                    conn.execute(
                        """
                        INSERT INTO trade_group_stats (
                            scope_key, group_id, collateral_book,
                            closed_ts_ms, close_book_equity, realized_pnl_usdc,
                            realized_apr_on_equity, holding_days, updated_ts_ms
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope_key,
                            group_id,
                            book,
                            int(closed_ts_ms),
                            str(close_book_equity),
                            str(realized_pnl_usdc),
                            str(realized_apr_on_equity),
                            str(holding_days),
                            now,
                        ),
                    )
                conn.commit()

    def get_group_stats(self, scope_key: str, group_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT collateral_book, opened_ts_ms, entry_book_equity, entry_net_apr,
                       entry_credit_usdc, closed_ts_ms, close_book_equity, realized_pnl_usdc,
                       realized_apr_on_equity, holding_days
                FROM trade_group_stats
                WHERE scope_key = ? AND group_id = ?
                """,
                (scope_key, group_id),
            ).fetchone()
        if not row:
            return None
        return {
            "collateral_book": row[0],
            "opened_ts_ms": row[1],
            "entry_book_equity": row[2],
            "entry_net_apr": row[3],
            "entry_credit_usdc": row[4],
            "closed_ts_ms": row[5],
            "close_book_equity": row[6],
            "realized_pnl_usdc": row[7],
            "realized_apr_on_equity": row[8],
            "holding_days": row[9],
        }

    def record_reconcile_close(
        self,
        *,
        scope_key: str,
        group_id: str,
        instrument_name: str,
        strategy: str,
        reason: str,
        quantity: Decimal,
        close_debit_usdc: Decimal | None,
        closed_timestamp_ms: int,
        realized_pnl: Decimal | None = None,
        close_premium_native: Decimal | None = None,
    ) -> None:
        """Log an exchange-side close detected without live bot order fills."""
        extra = {
            "close_debit_usdc": str(close_debit_usdc) if close_debit_usdc is not None else None,
            "realized_pnl_usdc": str(realized_pnl) if realized_pnl is not None else None,
            "synthetic": True,
        }
        if close_premium_native is not None and close_premium_native > 0:
            price = close_premium_native
        else:
            price = safe_div(close_debit_usdc or Decimal("0"), quantity)
        self.record_fill(
            scope_key=scope_key,
            event_type="close",
            source_action="reconcile_external",
            instrument_name=instrument_name,
            direction="buy",
            amount=quantity,
            price=price,
            group_id=group_id,
            leg="short",
            strategy=strategy,
            reason=reason,
            ts_ms=closed_timestamp_ms,
            extra=extra,
        )


_OPEN_ACTION_SUFFIXES = ("_entered",)
_CLOSE_ACTIONS = frozenset(
    {
        "close_group",
        "close_group_incomplete",
        "close_perp",
        "close_perp_preview",
    }
)
_HEDGE_ACTIONS = frozenset(
    {
        "hedge",
        "hedge_position_reconcile",
        "hedge_unwind",
        "close_perp",
        "close_perp_preview",
    }
)


def _event_type_for_action(action_name: str) -> str | None:
    if action_name in _CLOSE_ACTIONS:
        return "close"
    if any(action_name.endswith(suffix) for suffix in _OPEN_ACTION_SUFFIXES):
        return "open"
    if action_name in _HEDGE_ACTIONS:
        return "hedge"
    return None


def _iter_trade_batches(action: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    trades = action.get("trades")
    if isinstance(trades, dict):
        for leg, leg_trades in trades.items():
            if isinstance(leg_trades, list) and leg_trades:
                batches.append((str(leg), leg_trades))
    elif isinstance(trades, list) and trades:
        batches.append(("", trades))
    return batches


def _trades_from_responses(responses: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    out: list[tuple[str, list[dict[str, Any]]]] = []
    if not isinstance(responses, dict):
        return out
    for leg, payload in responses.items():
        if not isinstance(payload, dict):
            continue
        leg_trades = payload.get("trades")
        if isinstance(leg_trades, list) and leg_trades:
            out.append((str(leg), leg_trades))
        order = payload.get("order")
        if isinstance(order, dict):
            order_trades = order.get("trades")
            if isinstance(order_trades, list) and order_trades:
                out.append((str(leg), order_trades))
    return out


def ingest_engine_action(
    store: TradeJournalStore,
    *,
    scope_key: str,
    action: dict[str, Any],
    default_strategy: str = "",
) -> int:
    """Persist fills embedded in a single engine action payload."""
    action_name = str(action.get("action") or "")
    event_type = _event_type_for_action(action_name)
    if event_type is None:
        return 0

    group = action.get("group")
    group_id = ""
    strategy = default_strategy
    if isinstance(group, dict):
        group_id = str(group.get("group_id") or "")
        strategy = str(group.get("strategy") or strategy) or default_strategy
    elif action.get("group_id"):
        group_id = str(action.get("group_id"))

    reason = str(action.get("reason") or action.get("close_reason") or "")
    inserted = 0
    meta = {"engine_action": action_name}

    for leg, leg_trades in _iter_trade_batches(action):
        inserted += store.record_fills(
            scope_key=scope_key,
            event_type=event_type,
            source_action=action_name,
            trades=leg_trades,
            group_id=group_id,
            leg=leg,
            strategy=strategy,
            reason=reason,
            extra=meta,
        )

    responses = action.get("responses")
    if isinstance(responses, dict):
        for leg, leg_trades in _trades_from_responses(responses):
            inserted += store.record_fills(
                scope_key=scope_key,
                event_type=event_type,
                source_action=action_name,
                trades=leg_trades,
                group_id=group_id,
                leg=leg,
                strategy=strategy,
                reason=reason,
                extra=meta,
            )
        for key in ("short_attempts", "long_attempts"):
            attempts = responses.get(key)
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                for leg, leg_trades in _trades_from_responses({key: attempt}):
                    inserted += store.record_fills(
                        scope_key=scope_key,
                        event_type=event_type,
                        source_action=action_name,
                        trades=leg_trades,
                        group_id=group_id,
                        leg=leg,
                        strategy=strategy,
                        reason=reason,
                        extra=meta,
                    )
    response = action.get("response")
    if isinstance(response, dict):
        for leg, leg_trades in _trades_from_responses({"response": response}):
            inserted += store.record_fills(
                scope_key=scope_key,
                event_type=event_type,
                source_action=action_name,
                trades=leg_trades,
                group_id=group_id,
                leg=leg,
                strategy=strategy,
                reason=reason,
                extra=meta,
            )
    return inserted


def ingest_engine_actions(
    store: TradeJournalStore,
    *,
    scope_key: str,
    actions: list[dict[str, Any]],
    default_strategy: str = "",
) -> int:
    total = 0
    for action in actions:
        if isinstance(action, dict):
            total += ingest_engine_action(
                store,
                scope_key=scope_key,
                action=action,
                default_strategy=default_strategy,
            )
    return total
