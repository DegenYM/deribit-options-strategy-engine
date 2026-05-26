from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest import mock

from conftest import future_expiry

from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore


def _sample_state() -> StrategyState:
    state = StrategyState()
    group = TradeGroup(
        group_id="0001",
        currency="BTC",
        collateral_currency="USDC",
        quantity=None,  # type: ignore[arg-type]
        entry_timestamp_ms=1,
        expiration_timestamp_ms=future_expiry(7),
        short_instrument_name="BTC_USDC-14APR30-63000-P",
        short_strike=None,  # type: ignore[arg-type]
        entry_credit=None,  # type: ignore[arg-type]
        original_entry_credit=None,  # type: ignore[arg-type]
        max_loss=None,  # type: ignore[arg-type]
        regime_at_entry="normal",
    )
    # Populate the Decimal fields via the serialization round-trip helper so we
    # don't need to import Decimal just for this smoke state.
    from decimal import Decimal

    group.quantity = Decimal("0.1")
    group.short_strike = Decimal("63000")
    group.entry_credit = Decimal("10")
    group.original_entry_credit = Decimal("10")
    group.max_loss = Decimal("50")

    state.groups.append(group)
    return state


def test_save_creates_file_and_load_round_trips(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    state = _sample_state()

    store.save(state)
    assert store.path.exists()
    loaded = store.load()

    assert len(loaded.groups) == 1
    assert loaded.groups[0].short_instrument_name == "BTC_USDC-14APR30-63000-P"


def test_load_returns_empty_state_when_file_missing(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "does_not_exist.json")
    loaded = store.load()
    assert loaded.groups == []


def test_legacy_covered_call_adoption_label_is_normalized() -> None:
    group = TradeGroup.from_dict(
        {
            "group_id": "0001",
            "currency": "ETH",
            "collateral_currency": "ETH",
            "quantity": "1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": future_expiry(7),
            "short_instrument_name": "ETH-14APR30-2600-C",
            "short_strike": "2600",
            "entry_credit": "30",
            "original_entry_credit": "30",
            "max_loss": "250",
            "regime_at_entry": "normal",
            "option_type": "call",
            "strategy": "naked_short_call",
            "short_label": "covered_call-spread-eth-0001-short",
        }
    )

    assert group.strategy == "covered_call"
    assert group.covered_underlying_quantity == Decimal("1")


def test_save_is_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.save(_sample_state())

    # No leftover tmp after a clean save
    assert not store.tmp_path.exists()


def test_save_failure_cleans_tmp(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.save(_sample_state())
    first_content = store.path.read_text()

    with mock.patch("deribit_engine.state.os.replace", side_effect=OSError("boom")):
        try:
            store.save(_sample_state())
        except OSError:
            pass

    # Original file untouched
    assert store.path.read_text() == first_content
    # Failed write's .tmp was cleaned up
    assert not store.tmp_path.exists()


def test_load_quarantines_corrupt_json_and_returns_fresh_state(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not valid json")

    loaded = store.load()
    assert loaded.groups == []

    # Bad file was renamed aside with `.corrupt.<ts>` suffix.
    corrupt_files = list(tmp_path.glob("state.json.corrupt.*"))
    assert len(corrupt_files) == 1
    # Original path no longer contains the bad bytes.
    assert not store.path.exists() or store.path.read_text() != "{ not valid json"


def test_load_quarantines_non_object_payload(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps([1, 2, 3]))

    loaded = store.load()
    assert loaded.groups == []
    assert list(tmp_path.glob("state.json.corrupt.*"))


def test_save_persists_sorted_keys_for_deterministic_diffs(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.save(_sample_state())
    raw = store.path.read_text()

    payload = json.loads(raw)
    # sort_keys=True guarantees alphabetical top-level keys in the rendered JSON.
    rendered_keys = [
        line.strip().split(":", 1)[0].strip('"')
        for line in raw.splitlines()
        if line.strip().startswith('"') and ":" in line
    ]
    top_level = [k for k in rendered_keys if k in payload]
    assert top_level == sorted(top_level)


def test_lock_file_is_created_alongside_state(tmp_path: Path) -> None:
    store = StrategyStateStore(tmp_path / "state.json")
    store.save(_sample_state())
    # POSIX-only; the lock file should have been created lazily during save.
    assert store.lock_path.exists() or os.name == "nt"
