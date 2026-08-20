from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import DeribitClient
from .models import OptionInstrument
from .utils import to_decimal


def _iso_day(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TradingViewSeries:
    """Normalized candle series.

    - `t`: list of timestamps in ms
    - `o/h/l/c/v`: decimal-like numeric values (float/str/Decimal ok)
    """

    t: tuple[int, ...]
    o: tuple[Any, ...]
    h: tuple[Any, ...]
    l: tuple[Any, ...]
    c: tuple[Any, ...]
    v: tuple[Any, ...]
    status: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> TradingViewSeries:
        status = str(payload.get("status") or "")
        ticks = payload.get("ticks") or []
        return cls(
            t=tuple(int(x) for x in ticks if x is not None),
            o=tuple(payload.get("open") or []),
            h=tuple(payload.get("high") or []),
            l=tuple(payload.get("low") or []),
            c=tuple(payload.get("close") or []),
            v=tuple(payload.get("volume") or []),
            status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "t": list(self.t),
            "o": list(self.o),
            "h": list(self.h),
            "l": list(self.l),
            "c": list(self.c),
            "v": list(self.v),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TradingViewSeries:
        return cls(
            t=tuple(int(x) for x in (payload.get("t") or [])),
            o=tuple(payload.get("o") or []),
            h=tuple(payload.get("h") or []),
            l=tuple(payload.get("l") or []),
            c=tuple(payload.get("c") or []),
            v=tuple(payload.get("v") or []),
            status=str(payload.get("status") or ""),
        )

    def close_by_ts(self) -> dict[int, Any]:
        out: dict[int, Any] = {}
        for i, ts in enumerate(self.t):
            if i >= len(self.c):
                break
            out[int(ts)] = self.c[i]
        return out


class BacktestCache:
    """Filesystem JSON cache for public market data.

    Cache is intentionally simple and transparent so users can inspect it.
    """

    def __init__(self, root: Path):
        self.root = root

    def _path(self, *parts: str) -> Path:
        safe = [p.replace("/", "_") for p in parts]
        return self.root.joinpath(*safe)

    def load_json(self, *parts: str) -> dict[str, Any] | None:
        path = self._path(*parts)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def save_json(self, payload: dict[str, Any], *parts: str) -> Path:
        path = self._path(*parts)
        _ensure_parent(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return path


class BacktestDataClient:
    """High-level public data loader with caching."""

    def __init__(self, client: DeribitClient, *, cache: BacktestCache):
        self.client = client
        self.cache = cache

    def list_option_instruments(
        self,
        currency: str,
        *,
        include_expired: bool = True,
    ) -> list[OptionInstrument]:
        cache_key = f"instruments_{currency.upper()}_{'all' if include_expired else 'active'}"
        cached = self.cache.load_json("instruments", cache_key + ".json")
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return [OptionInstrument.from_api(item) for item in cached["items"] if isinstance(item, dict)]

        if include_expired:
            active = self.client.get_instruments(currency.upper(), kind="option", expired=False)
            expired = self.client.get_instruments(currency.upper(), kind="option", expired=True)
            # De-duplicate by instrument_name.
            by_name: dict[str, dict[str, Any]] = {}
            for item in (active or []) + (expired or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("instrument_name") or "")
                if not name:
                    continue
                by_name[name] = item
            items = list(by_name.values())
        else:
            items = self.client.get_instruments(currency.upper(), kind="option", expired=False)
        self.cache.save_json({"items": items}, "instruments", cache_key + ".json")
        return [OptionInstrument.from_api(item) for item in items if isinstance(item, dict)]

    def get_index_series(
        self,
        index_name: str,
        *,
        range_name: str,
    ) -> list[tuple[int, Any]]:
        cache_key = f"index_{index_name.lower()}_{range_name}.json"
        cached = self.cache.load_json("index", cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            out: list[tuple[int, Any]] = []
            for row in cached["items"]:
                if isinstance(row, list) and len(row) >= 2:
                    out.append((int(row[0]), row[1]))
            return out
        items = self.client.get_index_chart_data(index_name, range_name=range_name)
        self.cache.save_json({"items": items}, "index", cache_key)
        out2: list[tuple[int, Any]] = []
        for row in items or []:
            if isinstance(row, list) and len(row) >= 2:
                out2.append((int(row[0]), row[1]))
        return out2

    def get_dvol_series(
        self,
        currency: str,
        *,
        start_timestamp: int,
        end_timestamp: int,
        resolution: str = "1D",
    ) -> list[tuple[int, Any]]:
        cache_key = f"dvol_{currency.upper()}_{start_timestamp}_{end_timestamp}_{resolution}.json"
        cached = self.cache.load_json("dvol", cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("data"), list):
            out: list[tuple[int, Any]] = []
            for row in cached["data"]:
                if isinstance(row, list) and len(row) >= 5:
                    out.append((int(row[0]), row[4]))
            return out

        payload = self.client.get_volatility_index_data(
            currency.upper(),
            start_timestamp=int(start_timestamp),
            end_timestamp=int(end_timestamp),
            resolution=str(resolution),
        )
        data = []
        if isinstance(payload, dict):
            data = payload.get("data") or []
        self.cache.save_json({"data": data}, "dvol", cache_key)
        out2: list[tuple[int, Any]] = []
        for row in data or []:
            if isinstance(row, list) and len(row) >= 5:
                out2.append((int(row[0]), row[4]))
        return out2

    def get_tradingview_series(
        self,
        instrument_name: str,
        *,
        start_timestamp: int,
        end_timestamp: int,
        resolution: str,
    ) -> TradingViewSeries:
        cache_key = f"{instrument_name}_{start_timestamp}_{end_timestamp}_{resolution}.json"
        cached = self.cache.load_json("tv", cache_key)
        if isinstance(cached, dict) and "t" in cached:
            return TradingViewSeries.from_dict(cached)

        payload = self.client.get_tradingview_chart_data(
            instrument_name,
            start_timestamp=int(start_timestamp),
            end_timestamp=int(end_timestamp),
            resolution=str(resolution),
        )
        series = TradingViewSeries.from_api(payload if isinstance(payload, dict) else {})
        self.cache.save_json(series.to_dict(), "tv", cache_key)
        return series


def pick_nearest_value(series: Iterable[tuple[int, Any]], *, ts_ms: int) -> Any | None:
    """Pick the nearest value by timestamp (expects series sorted by ts)."""
    best: tuple[int, Any] | None = None
    for t, v in series:
        if best is None:
            best = (t, v)
            continue
        if abs(t - ts_ms) < abs(best[0] - ts_ms):
            best = (t, v)
    return best[1] if best is not None else None


def to_decimal_or_none(value: Any) -> Any | None:
    if value is None:
        return None
    d = to_decimal(value)
    return d
