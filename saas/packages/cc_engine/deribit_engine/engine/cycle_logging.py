"""Cycle-level logging and dedupe-signature helpers used by :mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from typing import Any

from ..models import RiskRegime
from .context import _MAX_SCAN_BLOCKER_LOG_LINES, LOG_REASON_NUMBER_RE, LOGGER


class CycleLoggingMixin:
    def _log_cycle_update(self, cycle_no: int, cycle_result: dict[str, Any], *, live: bool) -> None:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        manage_actions = cycle_result["manage"].get("actions", [])
        entry = cycle_result["entry"]
        log_extra = {"cycle": cycle_no, "regime": portfolio["regime"]}
        LOGGER.info(
            "run cycle=%s live=%s regime=%s open_groups=%s manage_actions=%s entry_action=%s",
            cycle_no,
            live,
            portfolio["regime"],
            len(status.get("trade_groups", [])),
            len(manage_actions),
            entry["action"],
            extra=log_extra,
        )
        if manage_actions:
            LOGGER.info(
                "run cycle=%s manage_action_types=%s",
                cycle_no,
                ",".join(action["action"] for action in manage_actions),
            )
        if entry.get("reason"):
            LOGGER.info("run cycle=%s entry_reason=%s", cycle_no, entry["reason"])
        regime_by_currency = portfolio.get("regime_by_currency") or {}
        regime_detail_by_currency = portfolio.get("regime_detail_by_currency") or {}
        for currency in sorted(regime_by_currency):
            regime_value = regime_by_currency[currency]
            if regime_value == RiskRegime.NORMAL.value:
                continue
            detail = regime_detail_by_currency.get(currency) or ()
            detail_text = "; ".join(detail) if detail else "(no detail)"
            LOGGER.info(
                "run cycle=%s [regime] %s=%s — %s",
                cycle_no,
                currency,
                regime_value,
                detail_text,
            )
        blockers = cycle_result["scan"].get("entry_blockers", [])
        if blockers and not cycle_result["scan"].get("candidates"):
            for line in blockers[:_MAX_SCAN_BLOCKER_LOG_LINES]:
                LOGGER.info("run cycle=%s [scan] %s", cycle_no, line)
            if len(blockers) > _MAX_SCAN_BLOCKER_LOG_LINES:
                LOGGER.info(
                    "run cycle=%s [scan] ... %s more blocker lines omitted",
                    cycle_no,
                    len(blockers) - _MAX_SCAN_BLOCKER_LOG_LINES,
                )
        self._log_cycle_candidates(cycle_no, cycle_result["scan"]["candidates"])
        topup_actions = cycle_result.get("topup", [])
        if topup_actions:
            LOGGER.info(
                "run cycle=%s topup_actions=%s",
                cycle_no,
                ",".join(a["action"] for a in topup_actions),
            )

    def _entry_skip_reason(self, portfolio: dict[str, Any], *, candidates: list[Any]) -> str:
        if portfolio.get("portfolio_wide_entry_halt"):
            if portfolio.get("cooling_down"):
                return "cooling_down"
            return "halt_limit_reached"
        if portfolio.get("hard_derisk"):
            return "hard_derisk"
        halted_by_ccy = portfolio.get("halt_new_entries_by_currency") or {}
        if candidates:
            blocked = sorted(
                {
                    (getattr(c, "currency", None) or (c.get("currency") if isinstance(c, dict) else "") or "").upper()
                    for c in candidates
                }
                - {""}
            )
            if blocked and all(halted_by_ccy.get(ccy, True) for ccy in blocked):
                return "currency_regime_or_crisis_halt"
        if halted_by_ccy and all(halted_by_ccy.values()):
            return "all_currencies_halted"
        return "halt_limit_reached"

    def _cycle_log_signature(self, cycle_result: dict[str, Any]) -> tuple[Any, ...]:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        scan = cycle_result["scan"]
        entry = cycle_result["entry"]
        return (
            portfolio["regime"],
            portfolio["halt_new_entries"],
            portfolio["hard_derisk"],
            portfolio["cooling_down"],
            self._normalized_log_reasons(portfolio.get("halt_entry_reasons", [])),
            tuple(
                sorted(
                    (
                        group["group_id"],
                        group["currency"],
                        group["short_instrument_name"],
                        str(group["quantity"]),
                        group["status"],
                    )
                    for group in status.get("trade_groups", [])
                )
            ),
            tuple(self._cycle_action_signature(action) for action in cycle_result["manage"].get("actions", [])),
            scan.get("candidate_count", 0),
            tuple(
                (
                    candidate["currency"],
                    candidate["short_instrument_name"],
                )
                for candidate in scan.get("candidates", [])[:3]
            ),
            self._normalized_log_reasons(scan.get("entry_blockers", [])),
            tuple(
                (
                    currency,
                    portfolio.get("regime_by_currency", {}).get(currency),
                    self._normalized_log_reasons(detail),
                )
                for currency, detail in sorted((portfolio.get("regime_detail_by_currency") or {}).items())
            ),
            (
                entry.get("action"),
                entry.get("reason"),
                self._cycle_entry_signature(entry),
            ),
            tuple(a.get("action") for a in cycle_result.get("topup", [])),
        )

    def _normalized_log_reasons(self, reasons: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self._normalize_log_reason(reason) for reason in reasons)

    @staticmethod
    def _normalize_log_reason(reason: str) -> str:
        normalized = reason.strip()
        if " (" in normalized:
            normalized = normalized.split(" (", 1)[0]
        if "; " in normalized:
            normalized = normalized.split("; ", 1)[0]
        normalized = LOG_REASON_NUMBER_RE.sub("#", normalized)
        return " ".join(normalized.split())

    def _cycle_action_signature(self, action: dict[str, Any]) -> tuple[Any, ...]:
        return (
            action.get("action"),
            action.get("group_id"),
            action.get("reason"),
            action.get("currency"),
            action.get("instrument_name"),
            action.get("short_instrument_name"),
        )

    def _cycle_entry_signature(self, entry: dict[str, Any]) -> tuple[Any, ...] | None:
        candidate = entry.get("candidate")
        if not isinstance(candidate, dict):
            return None
        return (
            candidate.get("currency"),
            candidate.get("short_instrument_name"),
        )
