"""Close-reason groupings shared by strategy pricing and execution."""

from __future__ import annotations

INCOME_EXIT_REASONS = frozenset({"take_profit", "time_exit", "early_exit_low_apr"})
DEFENSE_EXIT_REASONS = frozenset({"hard_stop", "soft_stop", "soft_stop_no_hedge", "panic_close"})
