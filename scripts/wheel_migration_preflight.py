#!/usr/bin/env python3
"""Read-only preflight for moving running covered_call accounts onto the shared wheel profile.

Run it on the server, against the live config and state, before the new image replaces the
running one — the bots can keep running. It writes nothing: state is parsed straight from
JSON (not through ``StrategyStateStore``, which quarantines a file it cannot parse), and it
touches no private endpoint. Public index prices are fetched unless ``--no-network``.

Per covered_call sub-account it answers:

- Does the config still load? A sub-account env with ``COVERED_CALL_CSP_PREMIUM_TARGET=spot``
  does not, now that the shared profile turns on ``CSP_PREMIUM_LADDER``.
- Which layer decides each wheel key, and which earlier values lose. ``.env.investor`` is
  loaded before the shared profile, so whatever it sets for the wheel is overridden.
- Every covered call whose coin was sold at expiry: whether the wheel picks it up on the first
  cycle (there is no age limit), how many put rounds it has run, what the ladder adds to the
  strike ceiling, premium already swapped into coin, and auto-restore buy orders still
  resting — with the wheel on, auto-restore stops reconciling those.
- Open puts, and how far spot is from each strike.

    python scripts/wheel_migration_preflight.py --investor jack
    python scripts/wheel_migration_preflight.py --all --json

Exit code 2 when any account's config fails to load, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deribit_engine.cash_secured_ops import (  # noqa: E402
    cash_secured_children,
    cash_secured_premium_ledger,
    cash_secured_strike_bounds,
    cash_secured_target_native,
    itm_sold_ready_for_cash_secured,
)
from deribit_engine.config import BotConfig, ConfigurationError, _env_values, load_config  # noqa: E402
from deribit_engine.csp_premium_swap_ops import csp_premium_swap_spent_usdc  # noqa: E402
from deribit_engine.env_layout import env_layer_paths, load_investor_manifest  # noqa: E402
from deribit_engine.models import StrategyState, TradeGroup  # noqa: E402
from deribit_engine.spot_restore_ops import covered_call_cover_native  # noqa: E402
from deribit_engine.utils import utc_now_ms  # noqa: E402

WHEEL_KEYS = (
    "COVERED_CALL_SPOT_EXIT_ENABLED",
    "COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED",
    "CSP_PREMIUM_LADDER",
    "COVERED_CALL_CSP_PREMIUM_TARGET",
    "COVERED_CALL_AUTO_SPOT_RESTORE_ENABLED",
    "COVERED_CALL_CSP_SELF_ASSIGN_ENABLED",
    "COVERED_CALL_CSP_SELF_ASSIGN_MAX_DTE",
    "COVERED_CALL_CSP_SELF_ASSIGN_MAX_SPREAD_RATIO",
    "COVERED_CALL_CSP_SELF_ASSIGN_CONFIRM_CYCLES",
    "COVERED_CALL_CSP_ACTIVE_ROLL_ENABLED",
    "COVERED_CALL_CSP_STRIKE_FLOOR_PCT",
    "ENABLE_TREND_ADAPTIVE_SELECTION",
    "MIN_NET_APR",
)
EFFECTIVE_FIELDS = (
    "risk_tier",
    "covered_call_spot_exit_enabled",
    "covered_call_itm_to_cash_secured_enabled",
    "covered_call_csp_premium_ladder",
    "covered_call_csp_premium_target",
    "covered_call_auto_spot_restore_enabled",
    "covered_call_csp_self_assign_enabled",
    "covered_call_csp_self_assign_max_dte",
    "covered_call_csp_self_assign_max_spread_ratio",
    "covered_call_csp_self_assign_confirm_cycles",
    "covered_call_csp_active_roll_enabled",
    "covered_call_csp_strike_floor_pct",
    "enable_trend_adaptive_selection",
    "min_net_apr",
)
STAGE_ZH = {
    "first_put": "從未接回：開接回後第一輪就會替它賣賣權",
    "next_put": "等下一張賣權：階梯馬上生效",
    "put_open": "賣權開著：這張到期後，下一張才用階梯",
    "not_eligible": "不會再賣賣權",
}
WARNING_ZH = {
    "spot_below_ceiling": "現貨低於履約價上限：窗口裡的賣權是價內的",
    "auto_restore_order_resting": "自動買回單還掛著：開接回後不再對帳，成交不會記進 state",
    "premium_swapped_into_coin": "有權利金已換成幣，階梯不計這部分",
}
ACCOUNT_WARNING_ZH = {
    "active_roll_reaches_open_puts": "主動換約開著：開著的賣權可能在結算前被換成套用階梯的新賣權",
}


def _dec(value: Decimal | None, places: int = 2) -> str | None:
    return None if value is None else f"{value:.{places}f}"


def _pct(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator <= 0:
        return None
    return f"{(numerator / denominator - 1) * 100:+.2f}"


def _day(ms: int | None) -> str | None:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d") if ms else None


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _status(group: TradeGroup) -> str:
    return str(group.status or "").lower()


def investor_ids() -> list[str]:
    root = REPO_ROOT / "config" / "investors"
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", ".")) and (path / "accounts.toml").is_file()
    )


def key_sources(account_env: Path, strategy: str) -> dict[str, list[dict[str, str]]]:
    """Every layer that sets a wheel key, in load order; the last one is what runs."""
    sources: dict[str, list[dict[str, str]]] = {}
    for layer in env_layer_paths(account_env, strategy):
        values = _env_values(layer)
        for key in WHEEL_KEYS:
            if key in values:
                sources.setdefault(key, []).append({"layer": _rel(layer), "value": values[key]})
    return sources


def overridden_values(sources: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    """Values an earlier layer set that differ from the one that runs — they silently lose."""
    lost = []
    for key, rows in sources.items():
        final = rows[-1]
        for row in rows[:-1]:
            if row["value"].strip().lower() != final["value"].strip().lower():
                lost.append(
                    {
                        "key": key,
                        "layer": row["layer"],
                        "value": row["value"],
                        "wins": final["layer"],
                        "runs": final["value"],
                    }
                )
    return lost


def read_state(path: Path) -> StrategyState | None:
    """Parse the state file without the store, so it is never locked, moved or rewritten."""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return StrategyState.from_dict(payload) if isinstance(payload, dict) else None


def fetch_index_prices(currencies: set[str], base_url: str) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for currency in sorted(currencies):
        url = f"{base_url.rstrip('/')}/api/v2/public/get_index_price?index_name={currency.lower()}_usd"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - public Deribit endpoint
                price = Decimal(str(json.loads(response.read())["result"]["index_price"]))
        except Exception:  # noqa: BLE001 - a missing price only drops the spot column
            continue
        if price > 0:
            prices[currency] = price
    return prices


def parent_row(
    parent: TradeGroup, groups: list[TradeGroup], config: BotConfig, index: Decimal | None
) -> dict[str, Any]:
    """One covered call whose coin was sold at expiry, as the engine's wheel will see it."""
    ready, reason = itm_sold_ready_for_cash_secured(parent, groups)
    children = cash_secured_children(groups, parent)
    closed = [child for child in children if _status(child) == "closed"]
    open_child = next((child for child in children if _status(child) == "open"), None)
    # Same quantity the live entry divides the ledger by (_pending_itm_cash_secured_actions).
    cover = covered_call_cover_native(parent)
    sold = cash_secured_target_native(parent, groups)
    qty_cap = cover if cover > 0 else sold
    quantity = qty_cap if qty_cap > 0 else sold
    floor = config.covered_call_csp_strike_floor_pct
    low, high = cash_secured_strike_bounds(parent.short_strike, floor)
    ledger = cash_secured_premium_ledger(parent, groups)
    if config.covered_call_csp_premium_ladder:
        ladder_low, ladder_high = cash_secured_strike_bounds(
            parent.short_strike, floor, premium_credit=ledger, quantity=quantity
        )
    else:
        ladder_low, ladder_high = low, high

    if open_child is not None:
        stage = "put_open"
    elif ready and not children and not str(parent.cash_secured_status or "").strip():
        stage = "first_put"
    elif ready:
        stage = "next_put"
    else:
        stage = "not_eligible"

    warnings = []
    if stage == "first_put" and index is not None and index < ladder_high:
        warnings.append("spot_below_ceiling")
    restore = str(parent.spot_restore_status or "").lower()
    if (
        restore in {"submitted", "pending"}
        and parent.spot_restore_order_id
        and config.covered_call_itm_to_cash_secured_enabled
    ):
        warnings.append("auto_restore_order_resting")
    swapped = sum((csp_premium_swap_spent_usdc(child) for child in closed), Decimal("0"))
    if swapped > 0:
        warnings.append("premium_swapped_into_coin")

    return {
        "group_id": parent.group_id,
        "currency": parent.currency.upper(),
        "strike": _dec(parent.short_strike, 0),
        "expired": _day(parent.expiration_timestamp_ms),
        "stage": stage,
        "reason": reason,
        "rounds_closed": len(closed),
        "open_put": open_child.short_instrument_name if open_child is not None else None,
        "quantity": _dec(quantity, 4),
        "ledger_usdc": _dec(ledger),
        "swapped_usdc": _dec(swapped),
        "window": [_dec(low, 0), _dec(high, 0)],
        "window_with_ladder": [_dec(ladder_low, 0), _dec(ladder_high, 0)],
        "ceiling_lift_pct": _pct(ladder_high, high),
        "index": _dec(index, 0),
        "spot_vs_ceiling_pct": _pct(index, ladder_high) if index is not None else None,
        "restore_status": restore or None,
        "restore_order_id": parent.spot_restore_order_id or None,
        "warnings": warnings,
    }


def open_put_rows(groups: list[TradeGroup], prices: dict[str, Decimal], now_ms: int) -> list[dict[str, Any]]:
    rows = []
    for child in groups:
        if not child.is_cash_secured_group() or _status(child) != "open":
            continue
        index = prices.get(child.currency.upper())
        dte = Decimal(int(child.expiration_timestamp_ms or 0) - now_ms) / Decimal(86_400_000)
        rows.append(
            {
                "group_id": child.group_id,
                "parent_group_id": child.cash_secured_from_group_id or None,
                "instrument": child.short_instrument_name,
                "strike": _dec(child.short_strike, 0),
                "expiry": _day(child.expiration_timestamp_ms),
                "dte_days": _dec(dte, 1),
                "index": _dec(index, 0),
                "itm": None if index is None else index < child.short_strike,
                "spot_vs_strike_pct": _pct(index, child.short_strike) if index is not None else None,
            }
        )
    return rows


def check_account(
    investor_id: str,
    spec: Any,
    prices_for: Callable[[set[str]], dict[str, Decimal]],
) -> dict[str, Any]:
    sources = key_sources(spec.env_path, spec.strategy)
    row: dict[str, Any] = {
        "investor": investor_id,
        "account": spec.slug,
        "live_enabled": spec.live_enabled,
        "env_file": _rel(spec.env_path),
        "config_error": None,
        "sources": sources,
        "overridden": overridden_values(sources),
    }
    try:
        config = load_config(spec.env_path, require_private=False)
    except ConfigurationError as exc:
        row["config_error"] = str(exc)
        return row
    row["effective"] = {name: str(getattr(config, name, None)) for name in EFFECTIVE_FIELDS}
    state_path = config.state_file if config.state_file.is_absolute() else REPO_ROOT / config.state_file
    state = read_state(state_path)
    row["state_file"] = _rel(state_path)
    row["state_found"] = state is not None
    groups = list(state.groups) if state is not None else []
    prices = prices_for({group.currency.upper() for group in groups if group.currency})
    row["parents"] = [
        parent_row(group, groups, config, prices.get(group.currency.upper()))
        for group in groups
        if group.is_covered_call_group() and str(group.spot_exit_status or "").lower() == "filled"
    ]
    row["open_puts"] = open_put_rows(groups, prices, utc_now_ms())
    # Without the active roll an open put is never touched before it settles, so the ladder
    # only ever reaches the next one. With it, a put can be bought back and replaced early.
    row["warnings"] = (
        ["active_roll_reaches_open_puts"]
        if config.covered_call_csp_premium_ladder and config.covered_call_csp_active_roll_enabled and row["open_puts"]
        else []
    )
    row["pending_premium_swaps"] = [
        group.group_id
        for group in groups
        if group.is_cash_secured_group()
        and str(group.csp_premium_swap_status or "").lower() in {"pending", "submitted"}
    ]
    return row


def render_text(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(f"== {row['investor']} / {row['account']}（{row['env_file']}）")
        for item in row["overridden"]:
            lines.append(
                f"  ⚠ {item['key']}：{item['layer']} 設 {item['value']}，被 {item['wins']} 的 {item['runs']} 蓋掉"
            )
        if row["config_error"]:
            lines.append(f"  ✗ 設定載入失敗，部署後這個帳戶起不來：{row['config_error']}")
            lines.append("")
            continue
        eff = row["effective"]
        lines.append(
            "  設定："
            f"tier={eff['risk_tier']}｜ITM 賣出={eff['covered_call_spot_exit_enabled']}"
            f"｜接回={eff['covered_call_itm_to_cash_secured_enabled']}"
            f"｜階梯={eff['covered_call_csp_premium_ladder']}"
            f"｜權利金去向={eff['covered_call_csp_premium_target']}"
            f"｜自動買回={eff['covered_call_auto_spot_restore_enabled']}"
            f"｜自我指派 {eff['covered_call_csp_self_assign_max_dte']} 天／價差 {eff['covered_call_csp_self_assign_max_spread_ratio']}"
            f"／確認 {eff['covered_call_csp_self_assign_confirm_cycles']} 輪"
            f"｜主動換約={eff['covered_call_csp_active_roll_enabled']}"
        )
        for warning in row.get("warnings", []):
            lines.append(f"  ⚠ {ACCOUNT_WARNING_ZH[warning]}")
        if not row["state_found"]:
            lines.append(f"  state：{row['state_file']} 不存在")
            lines.append("")
            continue
        stages = {stage: sum(1 for p in row["parents"] if p["stage"] == stage) for stage in STAGE_ZH}
        lines.append(
            f"  state：{row['state_file']}｜已賣出現貨的備兌 {len(row['parents'])} 組"
            f"（從未接回 {stages['first_put']}、等下一張 {stages['next_put']}、賣權開著 {stages['put_open']}、"
            f"不會再賣 {stages['not_eligible']}）"
        )
        for parent in row["parents"]:
            head = (
                f"    #{parent['group_id']} {parent['currency']} K={parent['strike']}（{parent['expired']} 到期）"
                f"已跑 {parent['rounds_closed']} 輪 → {STAGE_ZH[parent['stage']]}"
            )
            if parent["stage"] == "not_eligible":
                head += f"（{parent['reason']}）"
            lines.append(head)
            window = f"      窗口 {parent['window'][0]}–{parent['window'][1]}"
            if parent["window_with_ladder"] != parent["window"]:
                window += (
                    f" → 階梯 {parent['window_with_ladder'][0]}–{parent['window_with_ladder'][1]}"
                    f"（上限 {parent['ceiling_lift_pct']}%，計入 {parent['ledger_usdc']} USDC）"
                )
            if parent["index"] is not None:
                window += f"｜現貨 {parent['index']}（對上限 {parent['spot_vs_ceiling_pct']}%）"
            lines.append(window)
            for warning in parent["warnings"]:
                lines.append(f"      ⚠ {WARNING_ZH[warning]}")
        for put in row["open_puts"]:
            where = (
                ""
                if put["index"] is None
                else f"｜現貨對履約價 {put['spot_vs_strike_pct']}%{'（價內）' if put['itm'] else ''}"
            )
            lines.append(
                f"    開著的賣權 #{put['group_id']} {put['instrument']}，{put['expiry']} 到期（{put['dte_days']} 天）{where}"
            )
        if row["pending_premium_swaps"]:
            lines.append(
                f"    權利金換現貨還在 pending：{', '.join(row['pending_premium_swaps'])}"
                "（去向改成 usdc 後不會再執行，錢留在 USDC）"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only wheel migration preflight for covered_call accounts.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--investor", help="investor id under config/investors/")
    target.add_argument("--all", action="store_true", help="every investor with an accounts.toml")
    parser.add_argument("--account", help="only this sub-account slug")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-network", action="store_true", help="skip public index prices")
    parser.add_argument("--base-url", default="https://www.deribit.com", help="host for public index prices")
    args = parser.parse_args(argv)

    cache: dict[str, Decimal] = {}

    def prices_for(currencies: set[str]) -> dict[str, Decimal]:
        if args.no_network:
            return {}
        missing = {currency for currency in currencies if currency not in cache}
        if missing:
            cache.update(fetch_index_prices(missing, args.base_url))
        return {currency: cache[currency] for currency in currencies if currency in cache}

    rows = []
    for investor in investor_ids() if args.all else [args.investor]:
        manifest = load_investor_manifest(investor, repo_root=REPO_ROOT)
        for spec in manifest.accounts:
            if spec.strategy != "covered_call" or (args.account and spec.slug != args.account):
                continue
            rows.append(check_account(manifest.investor_id, spec, prices_for))
    print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else render_text(rows))
    return 2 if any(row["config_error"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
