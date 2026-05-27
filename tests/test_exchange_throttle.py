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
