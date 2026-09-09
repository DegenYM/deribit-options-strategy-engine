"""State file compaction + closed-group archival (FIX 2)."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import future_expiry

from deribit_engine import state as state_module
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import (
    StrategyStateStore,
    archive_closed_groups_for_state_file,
    closed_archive_path,
    closed_group_is_archivable,
    group_has_pending_follow_up,
    iter_all_groups,
    load_archived_groups,
    referenced_parent_group_ids,
    select_archivable_closed_groups,
    serialize_state,
)

DAY_MS = 86_400_000
NOW_MS = 1_800_000_000_000


def _group(gid: str, *, status: str = "open", closed_ms: int | None = None) -> TradeGroup:
    group = TradeGroup(
        group_id=gid,
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=NOW_MS - 400 * DAY_MS,
        expiration_timestamp_ms=future_expiry(7),
        short_instrument_name=f"BTC_USDC-14APR30-{gid}-P",
        short_strike=Decimal("63000"),
        entry_credit=Decimal("10"),
        original_entry_credit=Decimal("10"),
        max_loss=Decimal("50"),
        regime_at_entry="normal",
    )
    group.status = status
    group.closed_timestamp_ms = closed_ms
    if status == "closed":
        group.realized_pnl = Decimal("1.5")
    return group


def _strip_volatile(payload: dict) -> dict:
    """Drop keys derived from wall-clock time (``dte_days`` is recomputed on every to_dict)."""

    def _clean(obj):  # noqa: ANN001, ANN202
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items() if k != "dte_days"}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return obj

    return _clean(payload)


def _state_with_history() -> StrategyState:
    """2 open + 30 closed groups: 5 recent (<90d), 25 old (100..124 days)."""
    state = StrategyState()
    state.next_group_id = 100
    state.groups.append(_group("o1"))
    state.groups.append(_group("o2"))
    for i in range(5):
        state.groups.append(_group(f"r{i:02d}", status="closed", closed_ms=NOW_MS - (i + 1) * DAY_MS))
    for i in range(25):
        state.groups.append(_group(f"c{i:02d}", status="closed", closed_ms=NOW_MS - (100 + i) * DAY_MS))
    return state


# ---- compact serialization ----------------------------------------------------


def test_save_is_compact_by_default_and_loads_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STATE_JSON_PRETTY", raising=False)
    store = StrategyStateStore(tmp_path / "state.json")
    store.save(_state_with_history())
    raw = store.path.read_text(encoding="utf-8")
    assert "\n" not in raw.strip()
    assert ": " not in raw  # compact separators
    loaded = store.load()
    assert len(loaded.groups) == 32
    # Deterministic: re-serializing the loaded payload with sort_keys yields the same bytes.
    assert raw == json.dumps(json.loads(raw), separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def test_pretty_env_and_kwarg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATE_JSON_PRETTY", "true")
    store = StrategyStateStore(tmp_path / "a.json")
    assert store.pretty is True
    store.save(_state_with_history())
    assert store.path.read_text().count("\n") > 10

    # Explicit kwarg beats env.
    compact = StrategyStateStore(tmp_path / "b.json", pretty=False)
    assert compact.pretty is False
    compact.save(_state_with_history())
    assert compact.path.read_text().count("\n") == 0

    monkeypatch.setenv("STATE_JSON_PRETTY", "garbage")
    assert StrategyStateStore(tmp_path / "c.json").pretty is False


def test_load_accepts_pretty_and_compact_files(tmp_path: Path) -> None:
    state = _state_with_history()
    pretty_path = tmp_path / "p.json"
    pretty_path.write_text(serialize_state(state, pretty=True), encoding="utf-8")
    compact_path = tmp_path / "c.json"
    compact_path.write_text(serialize_state(state, pretty=False), encoding="utf-8")
    a = StrategyStateStore(pretty_path).load()
    b = StrategyStateStore(compact_path).load()
    assert [g.group_id for g in a.groups] == [g.group_id for g in b.groups]
    assert _strip_volatile(a.to_dict()) == _strip_volatile(b.to_dict())


def test_compact_is_materially_smaller(tmp_path: Path) -> None:
    state = _state_with_history()
    pretty = serialize_state(state, pretty=True)
    compact = serialize_state(state, pretty=False)
    assert len(compact) < len(pretty) * 0.8


# ---- selection ------------------------------------------------------------------


def test_select_archivable_respects_keep_days_and_keep_min() -> None:
    state = _state_with_history()
    picked = select_archivable_closed_groups(state.groups, keep_recent_days=90, keep_min=20, now_ms=NOW_MS)
    # 30 closed; newest 20 protected (5 recent + c00..c14); remaining c15..c24 are all > 90d.
    assert [g.group_id for g in picked] == [f"c{i:02d}" for i in range(24, 14, -1)]
    assert picked[0].group_id == "c24"  # oldest first
    assert all(g.status == "closed" for g in picked)


def test_select_archivable_keep_days_protects_recent_even_beyond_keep_min() -> None:
    state = _state_with_history()
    picked = select_archivable_closed_groups(state.groups, keep_recent_days=200, keep_min=0, now_ms=NOW_MS)
    assert picked == []
    picked = select_archivable_closed_groups(state.groups, keep_recent_days=0, keep_min=0, now_ms=NOW_MS)
    assert len(picked) == 30


def test_select_archivable_skips_open_and_closed_without_timestamp() -> None:
    groups = [_group("o"), _group("x", status="closed", closed_ms=None), _group("y", status="closed", closed_ms=1)]
    picked = select_archivable_closed_groups(groups, keep_recent_days=0, keep_min=0, now_ms=NOW_MS)
    assert [g.group_id for g in picked] == ["y"]


# ---- pending follow-up / wheel-parent guards ------------------------------------

OLD_MS = NOW_MS - 300 * DAY_MS


def _old_closed(gid: str, **fields: str) -> TradeGroup:
    group = _group(gid, status="closed", closed_ms=OLD_MS)
    for name, value in fields.items():
        setattr(group, name, value)
    return group


def _picked_ids(groups: list[TradeGroup]) -> list[str]:
    picked = select_archivable_closed_groups(groups, keep_recent_days=0, keep_min=0, now_ms=NOW_MS)
    return [g.group_id for g in picked]


@pytest.mark.parametrize("status", ["pending", "submitted", "failed", "weird_unknown"])
def test_pending_profit_sweep_is_retained(status: str) -> None:
    group = _old_closed("g", profit_sweep_status=status)
    assert group_has_pending_follow_up(group) is True
    assert closed_group_is_archivable(group, groups=[group]) is False
    assert _picked_ids([group]) == []


@pytest.mark.parametrize("status", ["pending", "submitted"])
def test_pending_spot_restore_is_retained(status: str) -> None:
    group = _old_closed("g", spot_restore_status=status)
    assert closed_group_is_archivable(group, groups=[group]) is False
    assert _picked_ids([group]) == []


@pytest.mark.parametrize("status", ["pending", "submitted"])
def test_pending_csp_premium_swap_is_retained(status: str) -> None:
    group = _old_closed("g", csp_premium_swap_status=status)
    assert closed_group_is_archivable(group, groups=[group]) is False
    assert _picked_ids([group]) == []


@pytest.mark.parametrize(
    ("attr", "status"),
    [
        ("spot_exit_status", "pending"),
        ("spot_exit_status", "submitted"),
        ("spot_exit_status", "failed"),
        ("cash_secured_status", "pending"),
        ("cash_secured_status", "submitted"),
    ],
)
def test_pending_spot_exit_and_cash_secured_are_retained(attr: str, status: str) -> None:
    group = _old_closed("g", **{attr: status})
    assert closed_group_is_archivable(group, groups=[group]) is False
    assert _picked_ids([group]) == []


def test_fully_terminal_old_group_is_archived() -> None:
    group = _old_closed(
        "g",
        profit_sweep_status="filled",
        spot_restore_status="skipped",
        csp_premium_swap_status="filled",
        spot_exit_status="filled",
        cash_secured_status="entered",
    )
    assert group_has_pending_follow_up(group) is False
    assert closed_group_is_archivable(group, groups=[group]) is True
    assert _picked_ids([group]) == ["g"]
    # Case / whitespace in the journal value must not flip the verdict.
    group.profit_sweep_status = " Filled "
    group.spot_restore_status = "CANCELLED"
    assert closed_group_is_archivable(group, groups=[group]) is True


def test_wheel_parent_referenced_by_retained_child_is_kept() -> None:
    parent = _old_closed("parent", spot_exit_status="filled", cash_secured_status="entered")
    open_child = _group("child_open")
    open_child.cash_secured_from_group_id = "parent"
    closed_child = _old_closed("child_closed", cash_secured_from_group_id="parent", profit_sweep_status="pending")

    groups = [parent, open_child, closed_child]
    assert referenced_parent_group_ids(groups) == {"parent"}
    assert closed_group_is_archivable(parent, groups=groups) is False
    # Only parents are protected: the closed child is judged on its own statuses.
    assert closed_group_is_archivable(closed_child, groups=groups) is False  # pending sweep
    closed_child.profit_sweep_status = "filled"
    assert closed_group_is_archivable(closed_child, groups=groups) is True
    assert _picked_ids(groups) == ["child_closed"]


def test_parent_becomes_archivable_once_children_leave_state(tmp_path: Path) -> None:
    """Run 1 archives the terminal child (parent retained); run 2 archives the parent."""
    store = StrategyStateStore(tmp_path / "wheel.json")
    parent = _old_closed("parent", spot_exit_status="filled", cash_secured_status="entered")
    child = _old_closed("child", cash_secured_from_group_id="parent")
    recent = _group("recent", status="closed", closed_ms=NOW_MS - DAY_MS)
    state = StrategyState(groups=[parent, child, recent], next_group_id=10)
    store.save(state)

    assert store.archive_closed_groups(state, keep_recent_days=90, keep_min=0, now_ms=NOW_MS) == 1
    assert [g.group_id for g in state.groups] == ["parent", "recent"]
    store.save(state)

    reloaded = store.load()
    assert store.archive_closed_groups(reloaded, keep_recent_days=90, keep_min=0, now_ms=NOW_MS) == 1
    assert [g.group_id for g in reloaded.groups] == ["recent"]
    store.save(reloaded)

    archived_ids = {g.group_id for g in load_archived_groups(store.path)}
    assert archived_ids == {"parent", "child"}
    assert {g.group_id for g in iter_all_groups(store.load(), store.path)} == {"parent", "child", "recent"}

    # Same when the child is simply removed from state rather than archived.
    other = StrategyState(groups=[_old_closed("p2"), _old_closed("c2", cash_secured_from_group_id="p2")])
    assert _picked_ids(other.groups) == ["c2"]
    other.groups = [g for g in other.groups if g.group_id != "c2"]
    assert _picked_ids(other.groups) == ["p2"]


# ---- archival -----------------------------------------------------------------


def test_archive_moves_groups_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "cc.json")
    state = _state_with_history()
    original_ids = {g.group_id for g in state.groups}
    original_dicts = {g.group_id: _strip_volatile(g.to_dict()) for g in state.groups}
    store.save(state)

    moved = store.archive_closed_groups(state, keep_recent_days=90, keep_min=20, now_ms=NOW_MS)
    assert moved == 10
    assert len(state.groups) == 22
    assert store.archive_path == tmp_path / "cc.closed_archive.jsonl"
    assert store.archive_path.is_file()
    store.save(state)

    archived = load_archived_groups(store.path)
    assert len(archived) == 10
    assert {g.group_id for g in archived} == {f"c{i:02d}" for i in range(15, 25)}
    for g in archived:
        assert _strip_volatile(g.to_dict()) == original_dicts[g.group_id]
    # load_archived_groups accepts the archive path itself too.
    assert len(load_archived_groups(store.archive_path)) == 10

    reloaded = store.load()
    assert {g.group_id for g in reloaded.groups} | {g.group_id for g in archived} == original_ids
    assert reloaded.next_group_id == 100

    # Second run: nothing eligible, archive unchanged.
    before = store.archive_path.read_bytes()
    assert store.archive_closed_groups(reloaded, keep_recent_days=90, keep_min=20, now_ms=NOW_MS) == 0
    assert store.archive_path.read_bytes() == before

    # iter_all_groups yields live + archived with no duplicates.
    all_ids = [g.group_id for g in iter_all_groups(reloaded, store.path)]
    assert len(all_ids) == len(set(all_ids)) == 32
    assert set(all_ids) == original_ids


def test_archive_does_not_duplicate_lines_after_crash_before_save(tmp_path: Path) -> None:
    """Crash between append and save → groups in both files; next run must not re-append."""
    store = StrategyStateStore(tmp_path / "cc.json")
    state = _state_with_history()
    store.save(state)
    assert store.archive_closed_groups(state, keep_recent_days=90, keep_min=20, now_ms=NOW_MS) == 10
    # Simulate crash: do NOT save. Reload from disk → groups still present.
    stale = store.load()
    assert len(stale.groups) == 32
    lines_before = store.archive_path.read_text().count("\n")

    assert store.archive_closed_groups(stale, keep_recent_days=90, keep_min=20, now_ms=NOW_MS) == 10
    assert store.archive_path.read_text().count("\n") == lines_before == 10
    store.save(stale)
    assert len(store.load().groups) == 22
    assert len(list(iter_all_groups(store.load(), store.path))) == 32


def test_archive_file_helper_loads_archives_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "cc.json"
    StrategyStateStore(path).save(_state_with_history())

    assert archive_closed_groups_for_state_file(path, keep_recent_days=90, keep_min=20, now_ms=NOW_MS) == 10
    assert len(StrategyStateStore(path).load().groups) == 22
    assert archive_closed_groups_for_state_file(path, keep_recent_days=90, keep_min=20, now_ms=NOW_MS) == 0
    assert archive_closed_groups_for_state_file(tmp_path / "missing.json") == 0


def test_load_archived_groups_skips_corrupt_lines(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "cc.json"
    archive = closed_archive_path(path)
    good = json.dumps(_group("g1", status="closed", closed_ms=1).to_dict(), default=str)
    archive.write_text(good + "\n{not json\n" + good + "\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger=state_module.__name__):
        groups = load_archived_groups(path)
    assert [g.group_id for g in groups] == ["g1"]
    assert "corrupt archive line 2" in caplog.text


def test_archive_append_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine import atomic_io

    calls: list[int] = []
    real = os.fsync

    def _fake(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(atomic_io.os, "fsync", _fake)
    store = StrategyStateStore(tmp_path / "cc.json")
    state = _state_with_history()
    store.archive_closed_groups(state, keep_recent_days=90, keep_min=20, now_ms=NOW_MS)
    assert calls, "archive append must fsync"


def test_save_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine import atomic_io

    calls: list[int] = []
    real = os.fsync

    def _fake(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(atomic_io.os, "fsync", _fake)
    StrategyStateStore(tmp_path / "s.json").save(StrategyState())
    assert len(calls) >= 1
