from __future__ import annotations

import json
from pathlib import Path

from deribit_engine.live_heartbeat import (
    LiveHeartbeatRecord,
    find_stale_heartbeats,
    heartbeat_path_for_state,
    iter_expected_live_heartbeats,
    read_live_heartbeat,
    write_live_heartbeat,
)
from deribit_engine.utils import utc_now_ms


def test_heartbeat_path_for_state():
    assert heartbeat_path_for_state(Path(".state/investors/demo/naked.json")) == Path(
        ".state/investors/demo/naked.heartbeat.json"
    )


def test_write_and_read_live_heartbeat(tmp_path: Path):
    path = tmp_path / "naked.heartbeat.json"
    record = LiveHeartbeatRecord(
        ts_ms=utc_now_ms(),
        cycle=3,
        regime="normal",
        last_error=None,
        investor_id="demo",
        slug="naked",
    )
    write_live_heartbeat(path, record)
    loaded = read_live_heartbeat(path)
    assert loaded is not None
    assert loaded.cycle == 3
    assert loaded.regime == "normal"
    assert loaded.investor_id == "demo"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ts_iso"]


def test_find_stale_heartbeats_missing_and_expired(tmp_path: Path):
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    investor_dir = tmp_path / "config" / "investors" / "alice"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alice"\ndisplay_name = "Alice"\n',
                "[[accounts]]",
                'slug = "naked"',
                'strategy = "naked_short"',
                "enabled = true",
                "live_enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    env_path = accounts_dir / ".env.naked"
    env_path.write_text(
        "\n".join(
            [
                "DERIBIT_CLIENT_ID=test",
                "DERIBIT_CLIENT_SECRET=secret",
                "STATE_FILE=.state/investors/alice/naked.json",
            ]
        ),
        encoding="utf-8",
    )

    expected = list(iter_expected_live_heartbeats(tmp_path, investor_id="alice"))
    assert len(expected) == 1
    assert expected[0].slug == "naked"

    now = utc_now_ms()
    stale = find_stale_heartbeats(tmp_path, stale_seconds=600, investor_id="alice", now_ms=now)
    assert len(stale) == 1
    assert stale[0].reason == "missing"

    heartbeat_path = expected[0].path
    write_live_heartbeat(
        heartbeat_path,
        LiveHeartbeatRecord(
            ts_ms=now - 900_000,
            cycle=1,
            regime="normal",
            last_error=None,
            investor_id="alice",
            slug="naked",
        ),
    )
    stale = find_stale_heartbeats(tmp_path, stale_seconds=600, investor_id="alice", now_ms=now)
    assert len(stale) == 1
    assert stale[0].reason == "expired"
    assert stale[0].age_seconds is not None
    assert stale[0].age_seconds > 600

    write_live_heartbeat(
        heartbeat_path,
        LiveHeartbeatRecord(
            ts_ms=now - 30_000,
            cycle=2,
            regime="normal",
            last_error=None,
            investor_id="alice",
            slug="naked",
        ),
    )
    assert find_stale_heartbeats(tmp_path, stale_seconds=600, investor_id="alice", now_ms=now) == []


def test_engine_writes_live_heartbeat_on_run(tmp_path, fake_client):
    from conftest import make_config

    from deribit_engine.engine import DeribitOptionTrialBot
    from deribit_engine.live_heartbeat import heartbeat_path_for_state, read_live_heartbeat

    config = make_config(
        tmp_path,
        state_file=tmp_path / ".state" / "investors" / "local" / "trial.json",
    )
    bot = DeribitOptionTrialBot(config, fake_client)
    bot.run(live=True, cycles=1)

    heartbeat_path = heartbeat_path_for_state(config.state_file)
    record = read_live_heartbeat(heartbeat_path)
    assert record is not None
    assert record.cycle == 1
    assert record.live is True
