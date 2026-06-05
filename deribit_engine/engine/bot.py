from __future__ import annotations

from .base import EngineBase
from .covered_call import CoveredCallMixin
from .entry import EntryMixin
from .execution import ExecutionMixin
from .management import ManagementMixin
from .scanner import ScannerMixin
from .state_reconcile import StateReconcileMixin


class DeribitOptionTrialBot(
    ExecutionMixin,
    ManagementMixin,
    CoveredCallMixin,
    StateReconcileMixin,
    EntryMixin,
    ScannerMixin,
    EngineBase,
):
    """Deribit options strategy bot — composed from scanner/entry/management/execution mixins."""
