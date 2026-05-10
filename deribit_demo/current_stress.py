from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .client import DeribitClient
from .config import BotConfig
from .exceptions import ExchangeError
from .models import AccountSummary, OptionInstrument, Position
from .stress import StressScenario, black_swan_strategy_analysis, stress_option_position_pnl_breakdown_usdc
from .utils import ONE, ZERO, parse_option_name, safe_div, to_decimal


@dataclass(frozen=True)
class CurrentStressResult:
    generated_at: str
    option_strategy: str
    strategy_analysis: dict[str, Any]
    index_by_ccy: dict[str, Decimal]
    equity_usdc_by_book: dict[str, Decimal]
    positions: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    notes: list[str]


def _index_name_for_currency(ccy: str) -> str:
    c = ccy.lower()
    if c in {"btc", "eth"}:
        return f"{c}_usd"
    return f"{c}_usd"


def _load_active_option_instruments(client: DeribitClient) -> dict[str, OptionInstrument]:
    by_name: dict[str, OptionInstrument] = {}
    for c in ("BTC", "ETH", "USDC"):
        try:
            items = client.get_instruments(c, kind="option", expired=False)
        except Exception:
            items = []
        for row in items or []:
            if not isinstance(row, dict):
                continue
            inst = OptionInstrument.from_api(row)
            if inst.instrument_name:
                by_name[inst.instrument_name] = inst
    return by_name


def _equity_by_book_usdc(
    config: BotConfig,
    *,
    summaries: dict[str, AccountSummary],
    index_by_ccy: dict[str, Decimal],
) -> dict[str, Decimal]:
    # Treat each sub-account as a segregated book keyed by collateral currency.
    out: dict[str, Decimal] = {}
    for book in config.traded_collaterals:
        c = (book or "").upper()
        summ = summaries.get(c)
        if summ is None:
            continue
        if c == "USDC":
            out[c] = summ.equity
        else:
            idx = index_by_ccy.get(c, ZERO)
            out[c] = summ.equity * idx if idx > 0 else ZERO
    return out


def compute_current_stress(
    config: BotConfig,
    client: DeribitClient,
    *,
    shocks: list[Decimal],
) -> CurrentStressResult:
    notes: list[str] = []

    # 1) Load account summaries + positions.
    summaries = {s.currency: s for s in (AccountSummary.from_api(x) for x in (client.get_account_summaries() or [])) if s.currency}
    positions = [Position.from_api(x) for x in (client.get_positions(currency="any", kind="any") or []) if isinstance(x, dict)]
    opt_positions = [p for p in positions if p.kind == "option" and p.instrument_name and p.size != 0]

    option_base_ccys: set[str] = set()
    for p in opt_positions:
        parsed = parse_option_name(p.instrument_name) or {}
        base = str(parsed.get("base_currency") or "") or p.instrument_name.split("-", 1)[0]
        base = base.split("_", 1)[0].upper()
        if base:
            option_base_ccys.add(base)

    # 2) Load index prices for collateral books and option underlyings.
    index_by_ccy: dict[str, Decimal] = {}
    for c in sorted({*(str(x).upper() for x in config.traded_collaterals), *option_base_ccys}):
        ccy = (c or "").upper()
        if ccy == "USDC":
            index_by_ccy[ccy] = ONE
            continue
        try:
            payload = client.get_index_price(_index_name_for_currency(ccy))
            idx = to_decimal(payload.get("index_price") if isinstance(payload, dict) else 0)
        except Exception:
            idx = ZERO
        if idx > 0:
            index_by_ccy[ccy] = idx

    # 3) Equity caps per book (USDC-equivalent).
    equity_usdc_by_book = _equity_by_book_usdc(config, summaries=summaries, index_by_ccy=index_by_ccy)
    total_equity = sum(equity_usdc_by_book.values(), ZERO) or config.reference_capital_usdc

    # 4) Load active instruments to get strike/settlement info.
    inst_by_name = _load_active_option_instruments(client)

    normalized_positions: list[dict[str, Any]] = []
    for p in opt_positions:
        parsed = parse_option_name(p.instrument_name) or {}
        base = str(parsed.get("base_currency") or "") or p.instrument_name.split("-", 1)[0]
        base = base.split("_", 1)[0].upper()
        inst = inst_by_name.get(p.instrument_name)
        if inst is None:
            # Fall back to parsed strike/type; infer linear USDC from names like
            # BTC_USDC-... when instrument metadata is unavailable.
            strike = to_decimal(parsed.get("strike"))
            opt_type = str(parsed.get("option_type") or "put")
            quote = str(parsed.get("quote_currency") or "").upper()
            settlement = "USDC" if quote == "USDC" else base
            instrument_type = "linear" if settlement == "USDC" else "reversed"
            contract_size = ONE
        else:
            strike = inst.strike
            opt_type = inst.option_type or str(parsed.get("option_type") or "put")
            settlement = inst.settlement_currency or ("USDC" if "_USDC-" in p.instrument_name else base)
            instrument_type = inst.instrument_type or ("linear" if settlement.upper() == "USDC" else "reversed")
            contract_size = inst.contract_size if inst.contract_size > 0 else ONE

        # Deribit option positions use ``size`` as the option amount. ``size_currency``
        # is a futures-oriented field and can have a different scale for options.
        qty = abs(p.size)
        normalized_positions.append(
            {
                "instrument_name": p.instrument_name,
                "base_currency": base,
                "settlement_currency": settlement.upper(),
                "instrument_type": instrument_type,
                "option_type": opt_type.lower(),
                "strike": strike,
                "contract_size": contract_size,
                "direction": p.direction,
                "quantity": qty,
                "mark_price": p.mark_price,
                "average_price": p.average_price,
                "floating_pnl": p.floating_profit_loss,
            }
        )

    # 5) Scenarios: staged slippage by shock magnitude (same ladder as backtest).
    def slippage_for(shock: Decimal) -> Decimal:
        a = abs(shock)
        if a <= Decimal("0.10"):
            return Decimal("0.05")
        if a <= Decimal("0.20"):
            return Decimal("0.10")
        if a <= Decimal("0.30"):
            return Decimal("0.15")
        if a <= Decimal("0.40"):
            return Decimal("0.20")
        if a <= Decimal("0.50"):
            return Decimal("0.25")
        return Decimal("0.30")

    scenario_rows: list[dict[str, Any]] = []
    for shock in shocks:
        slip = slippage_for(shock)
        sc = StressScenario(
            name=f"spot_{str(shock)}_slip_{str(slip)}",
            spot_shock=shock,
            liquidity_slippage=slip,
        )
        loss_by_book: dict[str, Decimal] = {}
        base_by_book: dict[str, Decimal] = {}
        slip_by_book: dict[str, Decimal] = {}
        spot_cover_by_book: dict[str, Decimal] = {}
        worst_legs: list[tuple[Decimal, dict[str, Any]]] = []

        for pos in normalized_positions:
            if pos["direction"] not in {"buy", "sell"}:
                continue
            base_ccy = pos["base_currency"]
            spot = index_by_ccy.get(base_ccy, ZERO)
            if spot <= 0:
                continue
            # Use *current* mark_price as baseline premium (P&L impact from now).
            bd = stress_option_position_pnl_breakdown_usdc(
                OptionInstrument(
                    instrument_name=pos["instrument_name"],
                    base_currency=base_ccy,
                    quote_currency="USDC" if pos["settlement_currency"] == "USDC" else base_ccy,
                    settlement_currency=pos["settlement_currency"],
                    instrument_type=pos["instrument_type"],
                    tick_size=Decimal("0.0001"),
                    tick_size_steps=(),
                    min_trade_amount=Decimal("0"),
                    contract_size=to_decimal(pos.get("contract_size") or 1),
                    option_type=pos["option_type"],
                    expiration_timestamp_ms=0,
                    strike=to_decimal(pos["strike"]),
                    instrument_state="open",
                ),
                option_type=pos["option_type"],
                quantity=to_decimal(pos["quantity"]),
                current_premium=to_decimal(pos["mark_price"]),
                spot=spot,
                scenario=sc,
                direction=pos["direction"],
            )
            total = to_decimal(bd["total_usdc"])
            book = pos["settlement_currency"]
            loss_by_book[book] = loss_by_book.get(book, ZERO) + total
            base_by_book[book] = base_by_book.get(book, ZERO) + to_decimal(bd["base_move_usdc"])
            slip_by_book[book] = slip_by_book.get(book, ZERO) + to_decimal(bd["slippage_usdc"])
            worst_legs.append((total, {**pos, "loss_usdc": total, "base_move_usdc": bd["base_move_usdc"], "slippage_usdc": bd["slippage_usdc"]}))

        if config.option_strategy == "covered_call":
            for book, equity_usdc in equity_usdc_by_book.items():
                if book == "USDC" or equity_usdc <= 0:
                    continue
                spot_pnl = equity_usdc * shock
                loss_by_book[book] = loss_by_book.get(book, ZERO) + spot_pnl
                spot_cover_by_book[book] = spot_cover_by_book.get(book, ZERO) + spot_pnl

        # Cap each book by its equity (cannot lose more than 100% of that book in liquidation).
        capped_total = ZERO
        capped_by_book: dict[str, Decimal] = {}
        capped_base_total = ZERO
        capped_slip_total = ZERO
        capped_spot_cover_total = ZERO
        for book, loss in loss_by_book.items():
            cap = equity_usdc_by_book.get(book, ZERO)
            capped = max(loss, -cap) if cap > 0 else loss
            capped_by_book[book] = capped
            capped_total += capped
            # Proportional cap of components
            raw = base_by_book.get(book, ZERO) + slip_by_book.get(book, ZERO) + spot_cover_by_book.get(book, ZERO)
            ratio = safe_div(capped, raw, ONE) if raw != 0 else ONE
            capped_base_total += base_by_book.get(book, ZERO) * ratio
            capped_slip_total += slip_by_book.get(book, ZERO) * ratio
            capped_spot_cover_total += spot_cover_by_book.get(book, ZERO) * ratio

        worst_legs_sorted = sorted(worst_legs, key=lambda x: x[0])[:5]
        scenario_rows.append(
            {
                "shock": str(shock),
                "slippage": str(slip),
                "loss_usdc_total": str(capped_total),
                "loss_usdc_pct_of_total_equity": str(safe_div(capped_total, total_equity, ZERO)),
                "loss_by_book_usdc": {k: str(v) for k, v in capped_by_book.items()},
                "components_total_usdc": {
                    "base_move_usdc": str(capped_base_total),
                    "slippage_usdc": str(capped_slip_total),
                    "spot_cover_usdc": str(capped_spot_cover_total),
                },
                "worst_legs": [item for _loss, item in worst_legs_sorted],
            }
        )

    return CurrentStressResult(
        generated_at="now",
        option_strategy=config.option_strategy,
        strategy_analysis=black_swan_strategy_analysis(config.option_strategy),
        index_by_ccy=index_by_ccy,
        equity_usdc_by_book=equity_usdc_by_book,
        positions=normalized_positions,
        scenarios=scenario_rows,
        notes=notes,
    )


def render_current_stress_md(result: CurrentStressResult) -> str:
    lines: list[str] = []
    lines.append("# 目前實際倉位：黑天鵝壓力測試（Deribit index）")
    lines.append("")
    analysis = result.strategy_analysis or black_swan_strategy_analysis(result.option_strategy)
    lines.append("## 策略黑天鵝解讀")
    lines.append(f"- strategy：`{result.option_strategy}`")
    lines.append(f"- 重點：{analysis.get('summary')}")
    lines.append(f"- 觀察方式：{analysis.get('focus')}")
    lines.append(f"- 模型備註：{analysis.get('model_note')}")
    lines.append("")
    lines.append("## Index 與 book equity（USDC 等值）")
    lines.append(f"- index：`{ {k: str(v) for k, v in result.index_by_ccy.items()} }`")
    lines.append(f"- equity_by_book_usdc：`{ {k: str(v) for k, v in result.equity_usdc_by_book.items()} }`")
    lines.append("")
    lines.append("## 目前 options 部位（節錄）")
    for p in result.positions[:20]:
        lines.append(f"- `{p['instrument_name']}` dir={p['direction']} qty={p['quantity']} mark={p['mark_price']} strike={p['strike']} settle={p['settlement_currency']}")
    lines.append("")
    lines.append("## 情境結果（由輕到重）")
    for s in result.scenarios:
        lines.append(
            f"- shock={s['shock']} slip={s['slippage']} → total_loss={s['loss_usdc_total']} "
            f"({to_decimal(s['loss_usdc_pct_of_total_equity'])*Decimal('100'):.2f}% of equity)"
        )
        lines.append(f"  - by_book={s['loss_by_book_usdc']}")
        lines.append(f"  - components={s['components_total_usdc']}")
        if s["worst_legs"]:
            top = s["worst_legs"][0]
            lines.append(f"  - worst_leg={top['instrument_name']} loss={top['loss_usdc']} base_move={top['base_move_usdc']} slip={top['slippage_usdc']}")
    lines.append("")
    lines.append("## 解讀")
    lines.append("- `base_move_usdc` 代表 option legs 在 shocked intrinsic 下的重估損益；short put 下跌會變差，long put 會抵銷。")
    lines.append("- `slippage_usdc` 是回補 short 或出清 long option 時的保守流動性折價。")
    lines.append("- `spot_cover_usdc` 只在 `covered_call` 下列入，用來估 BTC/ETH 現貨 cover 的 USDC 等值縮水。")
    lines.append("- 每本帳損失做了 equity 上限（最慘歸零），用來近似強制平倉/爆倉上限。")
    lines.append("")
    return "\n".join(lines)

