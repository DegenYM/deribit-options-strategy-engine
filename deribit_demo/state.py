from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
from pathlib import Path
from typing import Iterator

from .models import StrategyState
from .utils import json_default, utc_now_ms

try:
    import fcntl
except ImportError:  # pragma: no cover — POSIX only; Windows not supported by plan.
    fcntl = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


def performance_exclusions_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}.performance_exclusions.json")


def load_performance_exclusion_group_ids(state_path: Path) -> set[str]:
    path = performance_exclusions_path(state_path)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("failed to read performance exclusions at %s: %s", path, exc)
        return set()

    if isinstance(payload, list):
        raw_ids = payload
    elif isinstance(payload, dict):
        raw_ids = payload.get("excluded_group_ids") or payload.get("group_ids") or []
    else:
        raw_ids = []
    return {str(item) for item in raw_ids if str(item)}


class StrategyStateStore:
    """Atomic + locked persistence for StrategyState.

    - save: serialize to `<path>.tmp` then os.replace onto the real path; both steps happen
      inside an advisory file lock so concurrent bot instances don't interleave writes.
    - load: same lock while reading; if the JSON is corrupt the current file is moved to
      `<path>.corrupt.<ts>` and a fresh empty state is returned (with a warning logged).
    """

    def __init__(self, path: Path):
        self.path = path

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @property
    def tmp_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".tmp")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Advisory exclusive lock on a side-car file.

        Held for the full read/write so loads and saves serialize cleanly between processes.
        Falls back to a no-op lock if fcntl is not available (non-POSIX).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is None:  # pragma: no cover — POSIX is assumed.
            yield
            return

        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def load(self) -> StrategyState:
        if not self.path.exists():
            return StrategyState()
        try:
            with self._locked():
                raw = self.path.read_text()
                payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            backup = self._quarantine_corrupt_file(reason=f"JSONDecodeError: {exc}")
            LOGGER.warning(
                "strategy state file at %s is corrupt (%s); quarantined to %s and starting fresh",
                self.path,
                exc,
                backup,
            )
            return StrategyState()
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return StrategyState()
            raise

        if not isinstance(payload, dict):
            backup = self._quarantine_corrupt_file(reason="payload is not an object")
            LOGGER.warning(
                "strategy state at %s was not a JSON object; quarantined to %s",
                self.path,
                backup,
            )
            return StrategyState()
        try:
            return StrategyState.from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — defensive; schema drift or partial file.
            backup = self._quarantine_corrupt_file(reason=f"schema error: {exc}")
            LOGGER.warning(
                "failed to decode strategy state at %s (%s); quarantined to %s",
                self.path,
                exc,
                backup,
            )
            return StrategyState()

    def save(self, state: StrategyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            state.to_dict(),
            default=json_default,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with self._locked():
            tmp_path = self.tmp_path
            try:
                tmp_path.write_text(serialized, encoding="utf-8")
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise

    def _quarantine_corrupt_file(self, *, reason: str) -> Path:
        backup = self.path.with_suffix(self.path.suffix + f".corrupt.{utc_now_ms()}")
        try:
            self.path.replace(backup)
        except OSError as exc:  # pragma: no cover — best-effort.
            LOGGER.warning("unable to quarantine %s (%s): %s", self.path, reason, exc)
        return backup
