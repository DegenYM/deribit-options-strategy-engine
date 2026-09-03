from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .backtest_data import BacktestCache, BacktestDataClient
from .client import DeribitClient
from .config import BotConfig
from .utils import to_decimal


@dataclass(frozen=True)
class ParamVariant:
    name: str
    overrides: dict[str, Any]


def _clone_config(base: BotConfig, overrides: dict[str, Any]) -> BotConfig:
    data = dict(base.__dict__)
    data.update(overrides)
    return BotConfig(**data)


def _backtest_metrics(res: BacktestResult) -> dict[str, Any]:
    closes = [to_decimal(t.get("pnl_usdc") or 0) for t in res.trades if str(t.get("action", "")).startswith("close")]
    wins = sum(1 for p in closes if p > 0)
    win_rate = Decimal(str(wins)) / Decimal(str(len(closes))) if closes else Decimal("0")
    equities = [to_decimal(d.get("equity_usdc_end") or 0) for d in res.daily if d.get("equity_usdc_end") is not None]
    daily_returns: list[Decimal] = []
    for i in range(1, len(equities)):
        prev, cur = equities[i - 1], equities[i]
        if prev > 0:
            daily_returns.append((cur - prev) / prev)
    sharpe = Decimal("0")
    if len(daily_returns) >= 2:
        mean = sum(daily_returns) / Decimal(len(daily_returns))
        var = sum((r - mean) ** 2 for r in daily_returns) / Decimal(len(daily_returns) - 1)
        if var > 0:
            import math

            sharpe = (mean / Decimal(str(math.sqrt(float(var))))) * Decimal(str(math.sqrt(252.0)))
    return {"win_rate": win_rate, "sharpe": sharpe, "closed_trades": len(closes)}


def run_param_scan(
    base_config: BotConfig,
    client: DeribitClient,
    *,
    start: datetime,
    end: datetime,
    resolution: str,
    cache_root: str,
) -> list[dict[str, Any]]:
    cache = BacktestCache(root=(__import__("pathlib").Path(cache_root)))
    data = BacktestDataClient(client, cache=cache)
    bt = BacktestConfig(start=start, end=end, resolution=resolution, cache_root=cache_root)

    variants = [
        ParamVariant(
            name="baseline",
            overrides={},
        ),
        ParamVariant(
            name="conservative",
            overrides={
                "book_im_target": Decimal("0.30"),
                "book_im_hard": Decimal("0.40"),
                "per_leg_im_cap_put": Decimal("0.12"),
                "expiry_im_cap_per_book": Decimal("0.25"),
                "min_net_apr": Decimal("0.14"),
            },
        ),
        ParamVariant(
            name="profit_seek",
            overrides={
                "book_im_target": Decimal("0.38"),
                "book_im_hard": Decimal("0.50"),
                "per_leg_im_cap_put": Decimal("0.18"),
                "expiry_im_cap_per_book": Decimal("0.35"),
                "min_net_apr": Decimal("0.10"),
            },
        ),
        ParamVariant(
            name="iv_rank_gate",
            overrides={
                "enable_iv_entry_gate": True,
                "min_iv_rank": Decimal("0.35"),
                "min_iv_minus_rv": Decimal("0.02"),
            },
        ),
        ParamVariant(
            name="dynamic_tp",
            overrides={
                "enable_dynamic_tp": True,
                "tp_capture_pct_dte_long": Decimal("0.40"),
                "tp_capture_pct_dte_short": Decimal("0.65"),
            },
        ),
    ]

    out: list[dict[str, Any]] = []
    for v in variants:
        cfg = _clone_config(base_config, v.overrides)
        res: BacktestResult = run_backtest(cfg, data, bt, currencies=cfg.scan_underlyings or cfg.managed_currencies)
        stress = res.stress or {}
        shown = {
            "book_im_target": cfg.book_im_target,
            "book_im_hard": cfg.book_im_hard,
            "per_leg_im_cap_put": cfg.per_leg_im_cap_put,
            "expiry_im_cap_per_book": cfg.expiry_im_cap_per_book,
            "min_net_apr": cfg.min_net_apr,
        }
        metrics = _backtest_metrics(res)
        out.append(
            {
                "variant": v.name,
                "params": {**res.params, **shown},
                "total_pnl_usdc": res.params.get("total_pnl_usdc"),
                "max_drawdown_pct": res.params.get("max_drawdown_pct"),
                "win_rate": metrics["win_rate"],
                "sharpe": metrics["sharpe"],
                "closed_trades": metrics["closed_trades"],
                "stress": stress,
            }
        )
    return out
