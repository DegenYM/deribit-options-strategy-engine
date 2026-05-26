"""Live bot heartbeat files for external watchdog monitoring."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .env_layout import CONFIG_INVESTORS, default_state_file, load_investor_manifest
from .utils import json_default, ms_to_datetime, utc_now_ms

DEFAULT_STALE_SECONDS = 600


def heartbeat_path_for_state(state_file: Path) -> Path:
    return state_file.with_suffix(".heartbeat.json")


@dataclass(frozen=True)
class LiveHeartbeatRecord:
    ts_ms: int
    cycle: int
    regime: str | None
    last_error: str | None
    investor_id: str
    slug: str
    live: bool = True

    def to_dict(self) -> dict[str, Any]:
        ts_iso = None
        dt = ms_to_datetime(self.ts_ms)
        if dt is not None:
            ts_iso = dt.isoformat()
        return {
            "ts_ms": self.ts_ms,
            "ts_iso": ts_iso,
            "cycle": self.cycle,
            "regime": self.regime,
            "last_error": self.last_error,
            "investor_id": self.investor_id,
            "slug": self.slug,
            "live": self.live,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LiveHeartbeatRecord:
        return cls(
            ts_ms=int(payload.get("ts_ms") or 0),
            cycle=int(payload.get("cycle") or 0),
            regime=payload.get("regime"),
            last_error=payload.get("last_error"),
            investor_id=str(payload.get("investor_id") or ""),
            slug=str(payload.get("slug") or ""),
            live=bool(payload.get("live", True)),
        )


def write_live_heartbeat(path: Path, record: LiveHeartbeatRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(record.to_dict(), default=json_default, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    os.replace(tmp_path, path)


def read_live_heartbeat(path: Path) -> LiveHeartbeatRecord | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return LiveHeartbeatRecord.from_dict(payload)


@dataclass(frozen=True)
class ExpectedLiveHeartbeat:
    investor_id: str
    slug: str
    path: Path


@dataclass(frozen=True)
class StaleHeartbeat:
    investor_id: str
    slug: str
    path: Path
    age_seconds: float | None
    reason: str
    record: LiveHeartbeatRecord | None = None


def iter_expected_live_heartbeats(
    repo_root: Path | str,
    *,
    investor_id: str | None = None,
) -> Iterator[ExpectedLiveHeartbeat]:
    root = Path(repo_root)
    investors_root = root / CONFIG_INVESTORS
    if not investors_root.is_dir():
        return
    investor_dirs = sorted(path for path in investors_root.iterdir() if path.is_dir() and not path.name.startswith("."))
    for investor_dir in investor_dirs:
        iid = investor_dir.name
        if investor_id is not None and iid != investor_id:
            continue
        manifest_path = investor_dir / "accounts.toml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = load_investor_manifest(iid, repo_root=root)
        except Exception:
            continue
        for account in manifest.live_operational_accounts():
            state_path = root / default_state_file(iid, account.slug)
            yield ExpectedLiveHeartbeat(
                investor_id=iid,
                slug=account.slug,
                path=heartbeat_path_for_state(state_path),
            )


def find_stale_heartbeats(
    repo_root: Path | str,
    *,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    investor_id: str | None = None,
    now_ms: int | None = None,
) -> list[StaleHeartbeat]:
    now = now_ms if now_ms is not None else utc_now_ms()
    stale: list[StaleHeartbeat] = []
    for expected in iter_expected_live_heartbeats(repo_root, investor_id=investor_id):
        record = read_live_heartbeat(expected.path)
        if record is None:
            stale.append(
                StaleHeartbeat(
                    investor_id=expected.investor_id,
                    slug=expected.slug,
                    path=expected.path,
                    age_seconds=None,
                    reason="missing",
                )
            )
            continue
        age_ms = now - record.ts_ms
        age_seconds = age_ms / 1000.0
        if age_seconds > stale_seconds:
            stale.append(
                StaleHeartbeat(
                    investor_id=expected.investor_id,
                    slug=expected.slug,
                    path=expected.path,
                    age_seconds=age_seconds,
                    reason="expired",
                    record=record,
                )
            )
    return stale


def stale_seconds_from_environ() -> float:
    raw = os.environ.get("LIVE_HEARTBEAT_STALE_SECONDS", str(DEFAULT_STALE_SECONDS))
    try:
        return max(float(raw), 60.0)
    except (TypeError, ValueError):
        return float(DEFAULT_STALE_SECONDS)
