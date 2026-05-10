from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .backtest_data import BacktestCache, BacktestDataClient
from .client import DeribitClient
from .config import BotConfig


@dataclass(frozen=True)
class ParamVariant:
    name: str
    overrides: dict[str, Any]


def _clone_config(base: BotConfig, overrides: dict[str, Any]) -> BotConfig:
    data = dict(base.__dict__)
    data.update(overrides)
    return BotConfig(**data)


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
        out.append(
            {
                "variant": v.name,
                "params": {**res.params, **shown},
                "total_pnl_usdc": res.params.get("total_pnl_usdc"),
                "max_drawdown_pct": res.params.get("max_drawdown_pct"),
                "stress": stress,
            }
        )
    return out

