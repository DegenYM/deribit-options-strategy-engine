"""Live manage cycle wires ``StrategyStateStore.archive_closed_groups`` (opt-in).

Default config must never touch the archive; with
``STATE_CLOSED_ARCHIVE_ENABLED=true`` the live cycle archives exactly once per
manage call and old closed groups leave ``state.groups`` before the save.
"""

from __future__ import annotations

from decimal import Decimal

from conftest import FakeClient, future_expiry, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore, load_archived_groups
from deribit_engine.utils import utc_now_ms

DAY_MS = 86_400_000


def _closed_group(gid: str, *, age_days: int) -> TradeGroup:
    now_ms = utc_now_ms()
    group = TradeGroup(
        group_id=gid,
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=now_ms - (age_days + 10) * DAY_MS,
        expiration_timestamp_ms=future_expiry(7),
        short_instrument_name=f"BTC_USDC-14APR30-{gid}-P",
        short_strike=Decimal("63000"),
        entry_credit=Decimal("10"),
        original_entry_credit=Decimal("10"),
        max_loss=Decimal("50"),
        regime_at_entry="normal",
    )
    group.status = "closed"
    group.close_reason = "take_profit"
    group.closed_timestamp_ms = now_ms - age_days * DAY_MS
    group.realized_pnl = Decimal("1.5")
    return group


def _state_with_closed_history() -> StrategyState:
    """3 recent closed (1..3 days) + 5 old closed (200..204 days)."""
    state = StrategyState()
    state.next_group_id = 50
    for i in range(3):
        state.groups.append(_closed_group(f"recent{i}", age_days=i + 1))
    for i in range(5):
        state.groups.append(_closed_group(f"old{i}", age_days=200 + i))
    return state


class _CountingStore(StrategyStateStore):
    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self.archive_calls: list[dict] = []

    def archive_closed_groups(self, state, **kwargs):  # type: ignore[override]
        self.archive_calls.append(dict(kwargs))
        return super().archive_closed_groups(state, **kwargs)


def _engine(tmp_path, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    store = _CountingStore(config.state_file)
    store.save(_state_with_closed_history())
    engine = DeribitOptionTrialBot(config, FakeClient(), state_store=store)
    return engine, store


def test_default_config_never_archives(tmp_path):
    engine, store = _engine(tmp_path)
    assert engine.config.state_closed_archive_enabled is False

    engine.manage(live=True)

    assert store.archive_calls == []
    assert not store.archive_path.exists()
    assert len(store.load().groups) == 8


def test_enabled_archives_once_per_live_cycle_and_drops_old_groups(tmp_path):
    engine, store = _engine(
        tmp_path,
        state_closed_archive_enabled=True,
        state_closed_archive_keep_days=90,
        state_closed_archive_keep_min=3,
    )

    engine.manage(live=True)

    assert store.archive_calls == [{"keep_recent_days": 90, "keep_min": 3}]
    saved = store.load()
    assert sorted(g.group_id for g in saved.groups) == ["recent0", "recent1", "recent2"]
    archived = load_archived_groups(store.path)
    assert sorted(g.group_id for g in archived) == [f"old{i}" for i in range(5)]
    # The saved file must not contain the archived groups again.
    assert "old0" not in store.path.read_text(encoding="utf-8")

    # Second cycle: called again (once), nothing new to archive, archive unchanged.
    before = store.archive_path.read_bytes()
    engine.manage(live=True)
    assert len(store.archive_calls) == 2
    assert store.archive_path.read_bytes() == before
    assert len(store.load().groups) == 3


def test_enabled_but_dry_run_manage_does_not_archive(tmp_path):
    engine, store = _engine(tmp_path, state_closed_archive_enabled=True, state_closed_archive_keep_min=0)

    engine.manage(live=False)

    assert store.archive_calls == []
    assert len(store.load().groups) == 8
