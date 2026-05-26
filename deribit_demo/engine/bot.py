from __future__ import annotations

from .base import EngineBase
from .entry import EntryMixin
from .execution import ExecutionMixin
from .management import ManagementMixin
from .scanner import ScannerMixin


class DeribitOptionTrialBot(ExecutionMixin, ManagementMixin, EntryMixin, ScannerMixin, EngineBase):
    """Deribit options strategy bot — composed from scanner/entry/management/execution mixins."""
