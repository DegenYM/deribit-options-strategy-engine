from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from deribit_engine.live_heartbeat import read_live_heartbeat
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore
from deribit_engine.utils import dte_days, utc_now

from .settings import CoveredCallSettings

PERFORMANCE_DISCLAIMER_ZH = (
    "APR 與歷史績效不是收益承諾，也不是投資建議。沒有倉位或尚未同步帳戶時顯示 —。"
)


def _dec(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in {float("inf"), float("-inf")}:
        return None
    return n


def _credit_usdc(group: TradeGroup) -> Decimal | None:
    credit = group.entry_credit or Decimal("0")
    book = str(group.collateral_currency or group.currency or "").upper()
    if book in {"USDC", "USDT"}:
        return credit
    idx = group.entry_index_usd or Decimal("0")
    if idx > 0:
        return credit * idx
    return None


def empty_performance() -> dict[str, Any]:
    return {
        "has_data": False,
        "total_equity_usdc": None,
        "lifetime_pnl_usdc": None,
        "lifetime_apr": None,
        "open_credit_usdc": None,
        "win_rate": None,
        "avg_holding_days": None,
        "open_count": 0,
        "closed_count": 0,
        "realized_count": 0,
        "wins": 0,
        "since": None,
        "disclaimer_zh": PERFORMANCE_DISCLAIMER_ZH,
    }


def empty_snapshot(*, tenant_id: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "strategy": "covered_call",
        "risk_tier": None,
        "coins": [],
        "live": False,
        "paused": True,
        "open_groups": [],
        "closed_groups": [],
        "open_count": 0,
        "heartbeat": None,
        "performance": empty_performance(),
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }


def performance_from_state(state: StrategyState | None, *, now: datetime | None = None) -> dict[str, Any]:
    payload = empty_performance()
    if state is None:
        return payload
    clock = now or datetime.now(tz=UTC)
    now_ms = int(clock.timestamp() * 1000)
    open_groups = [group for group in state.groups if group.status == "open"]
    closed_groups = [group for group in state.groups if group.status != "open"]
    realized = [group for group in closed_groups if group.realized_pnl is not None]
    pnls = [group.realized_pnl for group in realized if group.realized_pnl is not None]
    credits = [converted for group in open_groups if (converted := _credit_usdc(group)) is not None]
    if not open_groups:
        open_credit: float | None = 0.0
    elif credits:
        open_credit = _num(sum(credits, Decimal("0")))
    else:
        open_credit = None

    equity = state.last_equity_usdc if state.last_equity_usdc and state.last_equity_usdc > 0 else None
    lifetime = sum(pnls, Decimal("0")) if pnls else None
    holdings: list[float] = []
    for group in closed_groups:
        if group.closed_timestamp_ms and group.entry_timestamp_ms and group.entry_timestamp_ms > 0:
            holdings.append(max(group.closed_timestamp_ms - group.entry_timestamp_ms, 0) / 86_400_000)

    entries = [
        group.entry_timestamp_ms
        for group in state.groups
        if group.entry_timestamp_ms and group.entry_timestamp_ms > 0
    ]
    since = None
    apr = None
    if entries:
        start_ms = min(entries)
        since = datetime.fromtimestamp(start_ms / 1000, tz=UTC).date().isoformat()
        sample_days = max(now_ms - start_ms, 0) / 86_400_000
        if lifetime is not None and equity is not None and sample_days > 0:
            apr = float(lifetime / equity * (Decimal("365") / Decimal(str(sample_days))))

    wins = sum(1 for pnl in pnls if pnl > 0)
    payload.update(
        {
            "has_data": bool(state.groups) or equity is not None,
            "total_equity_usdc": _num(equity),
            "lifetime_pnl_usdc": _num(lifetime),
            "lifetime_apr": apr,
            "open_credit_usdc": open_credit,
            "win_rate": (wins / len(pnls)) if pnls else None,
            "avg_holding_days": (sum(holdings) / len(holdings)) if holdings else None,
            "open_count": len(open_groups),
            "closed_count": len(closed_groups),
            "realized_count": len(realized),
            "wins": wins,
            "since": since,
        }
    )
    return payload


def _group_row(group: TradeGroup, *, now: datetime) -> dict[str, Any]:
    holding_days = None
    end_ms = group.closed_timestamp_ms if group.status != "open" else int(now.timestamp() * 1000)
    if group.entry_timestamp_ms and end_ms:
        holding_days = max(end_ms - group.entry_timestamp_ms, 0) / 86_400_000
    return {
        "group_id": group.group_id,
        "status": group.status,
        "currency": group.currency,
        "short_instrument": group.short_instrument_name,
        "strike": _dec(group.short_strike),
        "quantity": _dec(group.quantity),
        "entry_credit": _dec(group.entry_credit),
        "entry_credit_usdc": _dec(_credit_usdc(group)),
        "max_loss": _dec(group.max_loss),
        "realized_pnl": _dec(group.realized_pnl),
        "dte_days": str(dte_days(group.expiration_timestamp_ms, now=now)),
        "strategy": group.strategy,
        "option_type": group.option_type,
        "entry_timestamp_ms": group.entry_timestamp_ms or None,
        "closed_timestamp_ms": group.closed_timestamp_ms,
        "close_reason": group.close_reason or None,
        "holding_days": holding_days,
        "profit_sweep_status": getattr(group, "profit_sweep_status", None),
        "spot_exit_reason": getattr(group, "spot_exit_reason", None),
    }


def load_worker_snapshot(settings: CoveredCallSettings) -> dict[str, Any]:
    """Read local state + heartbeat without hitting Deribit (dashboard-safe)."""
    state: StrategyState | None = None
    if settings.state_file.is_file():
        try:
            state = StrategyStateStore(settings.state_file).load()
        except Exception as exc:  # noqa: BLE001
            payload = empty_snapshot(tenant_id=settings.tenant_id)
            payload["ok"] = False
            payload["error"] = f"state_unreadable: {exc}"
            return payload
    heartbeat = read_live_heartbeat(settings.heartbeat_file)
    now = utc_now()
    groups_out: list[dict[str, Any]] = []
    closed_out: list[dict[str, Any]] = []
    if state is not None:
        for group in state.groups:
            row = _group_row(group, now=now)
            if group.status == "open":
                groups_out.append(row)
            else:
                closed_out.append(row)
    paused = (settings.state_dir / "paused.json").is_file()
    return {
        "ok": True,
        "tenant_id": settings.tenant_id,
        "strategy": "covered_call",
        "risk_tier": settings.risk_tier,
        "coins": list(settings.coins),
        "live": settings.live,
        "paused": paused,
        "open_groups": groups_out,
        "closed_groups": closed_out[-20:],
        "open_count": len(groups_out),
        "heartbeat": heartbeat.to_dict() if heartbeat is not None else None,
        "performance": performance_from_state(state, now=now),
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }
