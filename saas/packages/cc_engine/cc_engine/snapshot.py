from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from deribit_engine.live_heartbeat import read_live_heartbeat
from deribit_engine.models import StrategyState
from deribit_engine.state import StrategyStateStore
from deribit_engine.utils import dte_days, utc_now

from .settings import CoveredCallSettings


def _dec(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def load_worker_snapshot(settings: CoveredCallSettings) -> dict[str, Any]:
    """Read local state + heartbeat without hitting Deribit (dashboard-safe)."""
    state: StrategyState | None = None
    if settings.state_file.is_file():
        try:
            state = StrategyStateStore(settings.state_file).load()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"state_unreadable: {exc}"}
    heartbeat = read_live_heartbeat(settings.heartbeat_file)
    now = utc_now()
    groups_out: list[dict[str, Any]] = []
    closed_out: list[dict[str, Any]] = []
    if state is not None:
        for group in state.groups:
            row = {
                "group_id": group.group_id,
                "status": group.status,
                "currency": group.currency,
                "short_instrument": group.short_instrument_name,
                "strike": _dec(group.short_strike),
                "quantity": _dec(group.quantity),
                "entry_credit": _dec(group.entry_credit),
                "dte_days": str(dte_days(group.expiration_timestamp_ms, now=now)),
                "strategy": group.strategy,
                "option_type": group.option_type,
                "profit_sweep_status": getattr(group, "profit_sweep_status", None),
                "spot_exit_reason": getattr(group, "spot_exit_reason", None),
            }
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
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }
