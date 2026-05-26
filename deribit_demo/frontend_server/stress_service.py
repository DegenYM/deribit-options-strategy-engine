from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..current_stress import CurrentStressResult
from ..engine import ExchangePrefetch
from ..models import (
    normalize_strategy_name,
)
from ..stress import black_swan_strategy_analysis
from ..utils import utc_now
from .constants import (
    STRATEGY_DISPLAY_ORDER,
)
from .types import (
    DashboardAccount,
    _TtlCache,
)

LOGGER = logging.getLogger(__name__)

from .helpers import (
    _dec,
    _decimalize,
    _has_private_creds,
    _live_api_identity,
    _live_api_identity_config,
    _ratio,
    _tag_rows,
)


def _new_stress_bucket(option_strategy: str, *, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    option_strategy = normalize_strategy_name(option_strategy, default=option_strategy)
    return {
        "option_strategy": option_strategy,
        "strategy_analysis": analysis or black_swan_strategy_analysis(option_strategy),
        "index_by_ccy": {},
        "equity_usdc_by_book": defaultdict(lambda: Decimal("0")),
        "positions": [],
        "scenarios_by_key": {},
        "notes": [],
        "accounts": [],
    }


def _add_stress_result(bucket: dict[str, Any], account: DashboardAccount, result: Any) -> None:
    bucket["accounts"].append(
        {
            "name": account.name,
            "env": account.config.env,
            "option_strategy": account.config.option_strategy,
        }
    )
    for ccy, value in result.index_by_ccy.items():
        key = str(ccy).upper()
        if _dec(value) > 0:
            bucket["index_by_ccy"][key] = value
    for book, value in result.equity_usdc_by_book.items():
        bucket["equity_usdc_by_book"][str(book).upper()] += value
    bucket["positions"].extend(_tag_rows(_decimalize(result.positions), account))
    bucket["notes"].extend(f"{account.name}: {note}" for note in result.notes)

    scenario_by_key = bucket["scenarios_by_key"]
    for scenario in _decimalize(result.scenarios):
        key = (str(scenario.get("shock")), str(scenario.get("slippage")))
        scenario_bucket = scenario_by_key.setdefault(
            key,
            {
                "shock": scenario.get("shock"),
                "slippage": scenario.get("slippage"),
                "loss_usdc_total": Decimal("0"),
                "loss_by_book_usdc": defaultdict(lambda: Decimal("0")),
                "components_total_usdc": defaultdict(lambda: Decimal("0")),
                "worst_legs": [],
            },
        )
        scenario_bucket["loss_usdc_total"] += _dec(scenario.get("loss_usdc_total"))
        for book, value in (scenario.get("loss_by_book_usdc") or {}).items():
            scenario_bucket["loss_by_book_usdc"][str(book).upper()] += _dec(value)
        for component, value in (scenario.get("components_total_usdc") or {}).items():
            scenario_bucket["components_total_usdc"][str(component)] += _dec(value)
        scenario_bucket["worst_legs"].extend(_tag_rows(scenario.get("worst_legs") or [], account))


def _finalize_stress_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    equity = dict(bucket["equity_usdc_by_book"])
    total_equity = sum(equity.values(), Decimal("0"))
    scenarios: list[dict[str, Any]] = []
    for scenario_bucket in bucket["scenarios_by_key"].values():
        total_loss = scenario_bucket["loss_usdc_total"]
        worst_legs = sorted(scenario_bucket["worst_legs"], key=lambda row: _dec(row.get("loss_usdc")))[:5]
        scenarios.append(
            {
                "shock": scenario_bucket["shock"],
                "slippage": scenario_bucket["slippage"],
                "loss_usdc_total": total_loss,
                "loss_usdc_pct_of_total_equity": _ratio(total_loss, total_equity),
                "loss_by_book_usdc": dict(scenario_bucket["loss_by_book_usdc"]),
                "components_total_usdc": dict(scenario_bucket["components_total_usdc"]),
                "worst_legs": worst_legs,
            }
        )
    scenarios.sort(key=lambda row: (_dec(row.get("shock")), _dec(row.get("slippage"))))
    return {
        "generated_at": utc_now(),
        "option_strategy": bucket["option_strategy"],
        "strategy_analysis": _decimalize(bucket["strategy_analysis"]),
        "index_by_ccy": dict(bucket["index_by_ccy"]),
        "equity_usdc_by_book": equity,
        "positions": bucket["positions"],
        "scenarios": scenarios,
        "notes": bucket["notes"],
        "accounts": bucket["accounts"],
    }


def _stress_result_payload(result: CurrentStressResult) -> dict[str, Any]:
    return {
        "generated_at": result.generated_at,
        "option_strategy": result.option_strategy,
        "strategy_analysis": _decimalize(result.strategy_analysis),
        "index_by_ccy": {k: str(v) for k, v in result.index_by_ccy.items()},
        "equity_usdc_by_book": {k: str(v) for k, v in result.equity_usdc_by_book.items()},
        "positions": _decimalize(result.positions),
        "scenarios": _decimalize(result.scenarios),
        "notes": list(result.notes),
    }


def _stress_cache_key(account: DashboardAccount) -> str:
    return f"{_live_api_identity(account)}\0{normalize_strategy_name(account.config.option_strategy)}"


def _stress_result_for_account(
    account: DashboardAccount,
    *,
    shocks: list[Decimal],
    prefetches: dict[str, ExchangePrefetch | None],
    results_by_identity: dict[str, CurrentStressResult],
) -> CurrentStressResult:
    cache_key = _stress_cache_key(account)
    cached = results_by_identity.get(cache_key)
    if cached is not None:
        return cached
    cfg = account.config
    prefetch = prefetches.get(_live_api_identity(account))
    import deribit_demo.frontend_server as pkg

    if prefetch is not None:
        result = pkg.compute_stress_from_prefetch(
            cfg,
            prefetch,
            shocks=shocks,
            client=pkg.DeribitClient(cfg),
        )
    else:
        result = pkg.compute_current_stress(cfg, pkg.DeribitClient(cfg), shocks=shocks)
    results_by_identity[cache_key] = result
    return result


def _aggregate_stress(
    accounts: list[DashboardAccount],
    *,
    shocks: list[Decimal],
    exchange_prefetch_cache: _TtlCache | None = None,
) -> dict[str, Any]:
    aggregate = _new_stress_bucket(
        "multi_account",
        analysis={
            "label": "multi_account",
            "summary": "Aggregated stress across the configured strategy sub-accounts.",
            "focus": "Use the per-strategy cards below to compare naked put, put spread, and covered call tail exposure.",
        },
    )
    strategy_buckets: dict[str, dict[str, Any]] = {}
    aggregate_identity: set[str] = set()
    import deribit_demo.frontend_server as pkg

    prefetches = (
        pkg._prefetch_all_accounts(accounts, cache=exchange_prefetch_cache)
        if exchange_prefetch_cache is not None
        else {}
    )
    results_by_identity: dict[str, CurrentStressResult] = {}

    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        result = _stress_result_for_account(
            account,
            shocks=shocks,
            prefetches=prefetches,
            results_by_identity=results_by_identity,
        )
        cfg = account.config
        strategy = normalize_strategy_name(result.option_strategy or cfg.option_strategy)
        strategy_bucket = strategy_buckets.setdefault(
            strategy, _new_stress_bucket(strategy, analysis=result.strategy_analysis)
        )
        ident = _live_api_identity_config(cfg, account.name)
        if ident not in aggregate_identity:
            aggregate_identity.add(ident)
            _add_stress_result(aggregate, account, result)
        _add_stress_result(strategy_bucket, account, result)

    payload = _finalize_stress_bucket(aggregate)
    order_rank = {name: index for index, name in enumerate(STRATEGY_DISPLAY_ORDER)}
    ordered_buckets = sorted(
        strategy_buckets.values(),
        key=lambda bucket: order_rank.get(
            normalize_strategy_name(bucket.get("option_strategy") or ""),
            len(STRATEGY_DISPLAY_ORDER),
        ),
    )
    payload["strategy_stresses"] = [_finalize_stress_bucket(bucket) for bucket in ordered_buckets]
    return payload
