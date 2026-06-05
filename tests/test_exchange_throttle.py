from __future__ import annotations

import pytest

from deribit_engine import exchange_throttle


def test_pace_exchange_request_spaces_calls(monkeypatch):
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.05")
    exchange_throttle._global_last_request_monotonic = 0.0
    sleeps: list[float] = []
    monotonic = iter([0.05, 0.06, 0.07, 0.08])
    monkeypatch.setattr(
        "deribit_engine.exchange_throttle.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "deribit_engine.exchange_throttle.time.monotonic",
        lambda: next(monotonic),
    )

    exchange_throttle.pace_exchange_request()
    exchange_throttle.pace_exchange_request()

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.04, abs=0.001)


def test_note_rate_limited_widens_interval_then_caps(monkeypatch):
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    exchange_throttle.reset_adaptive_backoff()

    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.10)
    exchange_throttle.note_rate_limited("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.20)
    exchange_throttle.note_rate_limited("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.40)
    # Capped at the configured maximum.
    exchange_throttle.note_rate_limited("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.50)


def test_note_success_decays_back_to_base(monkeypatch):
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    exchange_throttle.reset_adaptive_backoff()
    for _ in range(3):
        exchange_throttle.note_rate_limited("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.50)

    exchange_throttle.note_success("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.25)
    # Decay below base snaps back to the base interval (penalty cleared).
    exchange_throttle.note_success("acct")
    exchange_throttle.note_success("acct")
    assert exchange_throttle.adaptive_interval_seconds("acct") == pytest.approx(0.10)


def test_adaptive_backoff_per_identity_isolated(monkeypatch):
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    exchange_throttle.reset_adaptive_backoff()

    exchange_throttle.note_rate_limited("a")

    assert exchange_throttle.adaptive_interval_seconds("a") == pytest.approx(0.20)
    assert exchange_throttle.adaptive_interval_seconds("b") == pytest.approx(0.10)


def test_adaptive_backoff_disabled_when_pacing_off(monkeypatch):
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0")
    exchange_throttle.reset_adaptive_backoff()

    exchange_throttle.note_rate_limited("acct")

    assert exchange_throttle.adaptive_interval_seconds("acct") == 0.0
