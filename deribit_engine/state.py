from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from .models import StrategyState, TradeGroup
from .utils import json_default, utc_now_ms

try:
    import fcntl
except ImportError:  # pragma: no cover — POSIX only; Windows not supported by plan.
    fcntl = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

# Live manage cycles load state at start and save at end. Concurrent CLI tools
# (spot-restore / profit-sweep / …) may update the same file mid-cycle; a naive
# save would clobber those fields. On save we re-read disk under the lock and
# merge "operator journal" clusters when disk is ahead.
_JOURNAL_STATUS_RANK = {
    "": 0,
    "pending": 1,
    "submitted": 2,
    "failed": 2,
    "filled": 3,
}

_SPOT_EXIT_FIELDS = (
    "spot_exit_status",
    "spot_exit_amount",
    "spot_exit_instrument_name",
    "spot_exit_order_id",
    "spot_exit_reason",
    "spot_exit_quote_proceeds",
    "spot_exit_quote_proceeds_lifetime",
    "spot_exit_settlement_loss",
    "spot_exit_settlement_loss_source",
)
_SPOT_RESTORE_FIELDS = (
    "spot_restore_status",
    "spot_restore_amount",
    "spot_restore_instrument_name",
    "spot_restore_order_id",
    "spot_restore_reason",
    "spot_restore_quote_spent",
    "spot_restore_quote_spent_lifetime",
)
_PROFIT_SWEEP_FIELDS = (
    "profit_sweep_status",
    "profit_sweep_amount",
    "profit_sweep_instrument_name",
    "profit_sweep_order_id",
    "profit_sweep_quote_proceeds",
    "profit_sweep_quote_proceeds_lifetime",
    "profit_sweep_exchange_native",
    "profit_sweep_exchange_quote_proceeds",
    "profit_sweep_reason",
)


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


def _status_rank(status: str | None) -> int:
    return _JOURNAL_STATUS_RANK.get(str(status or "").strip().lower(), 0)


def _copy_group_fields(dst: TradeGroup, src: TradeGroup, fields: tuple[str, ...]) -> None:
    for name in fields:
        setattr(dst, name, getattr(src, name))


def _merge_journal_cluster(
    memory: TradeGroup,
    disk: TradeGroup,
    *,
    status_attr: str,
    fields: tuple[str, ...],
    amount_attrs: tuple[str, ...],
) -> bool:
    """Prefer disk when it is ahead on status, or richer on amounts at same status."""
    mem_rank = _status_rank(getattr(memory, status_attr))
    disk_rank = _status_rank(getattr(disk, status_attr))
    if disk_rank > mem_rank:
        _copy_group_fields(memory, disk, fields)
        return True
    if disk_rank < mem_rank:
        return False

    changed = False
    for name in amount_attrs:
        disk_val = getattr(disk, name)
        mem_val = getattr(memory, name)
        if isinstance(disk_val, Decimal) and isinstance(mem_val, Decimal) and disk_val > mem_val:
            setattr(memory, name, disk_val)
            changed = True
    # Fill missing string metadata from disk when amounts/status already match.
    for name in fields:
        if name == status_attr or name in amount_attrs:
            continue
        disk_val = getattr(disk, name)
        mem_val = getattr(memory, name)
        if isinstance(disk_val, str) and disk_val and not mem_val:
            setattr(memory, name, disk_val)
            changed = True
    if changed and disk_rank > 0 and not getattr(memory, status_attr):
        setattr(memory, status_attr, getattr(disk, status_attr))
    return changed


def merge_concurrent_group_updates(memory: StrategyState, disk: StrategyState) -> list[str]:
    """Merge CLI/operator journal fields from ``disk`` into ``memory``.

    Returns group ids that received at least one cluster merge.
    """
    disk_by_id = {str(g.group_id): g for g in disk.groups if g.group_id}
    merged_ids: list[str] = []
    for group in memory.groups:
        gid = str(group.group_id or "")
        other = disk_by_id.get(gid)
        if other is None:
            continue
        touched = False
        touched |= _merge_journal_cluster(
            group,
            other,
            status_attr="spot_restore_status",
            fields=_SPOT_RESTORE_FIELDS,
            amount_attrs=(
                "spot_restore_amount",
                "spot_restore_quote_spent",
                "spot_restore_quote_spent_lifetime",
            ),
        )
        touched |= _merge_journal_cluster(
            group,
            other,
            status_attr="profit_sweep_status",
            fields=_PROFIT_SWEEP_FIELDS,
            amount_attrs=(
                "profit_sweep_amount",
                "profit_sweep_quote_proceeds",
                "profit_sweep_quote_proceeds_lifetime",
                "profit_sweep_exchange_native",
                "profit_sweep_exchange_quote_proceeds",
            ),
        )
        touched |= _merge_journal_cluster(
            group,
            other,
            status_attr="spot_exit_status",
            fields=_SPOT_EXIT_FIELDS,
            amount_attrs=(
                "spot_exit_amount",
                "spot_exit_quote_proceeds",
                "spot_exit_quote_proceeds_lifetime",
                "spot_exit_settlement_loss",
            ),
        )
        if touched:
            merged_ids.append(gid)

    # Avoid regressing id allocation if another writer advanced it.
    if disk.next_group_id > memory.next_group_id:
        memory.next_group_id = disk.next_group_id
    return merged_ids


class StrategyStateStore:
    """Atomic + locked persistence for StrategyState.

    - save: serialize to `<path>.tmp` then os.replace onto the real path; both steps happen
      inside an advisory file lock so concurrent bot instances don't interleave writes.
      Before writing, re-read disk and merge operator journal fields (spot_restore /
      profit_sweep / spot_exit) so a long-lived live cycle cannot clobber mid-cycle CLI updates.
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

    def _decode_payload(self, payload: object) -> StrategyState | None:
        if not isinstance(payload, dict):
            return None
        try:
            return StrategyState.from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — defensive; schema drift or partial file.
            LOGGER.warning("failed to decode strategy state at %s (%s)", self.path, exc)
            return None

    def _read_unlocked(self) -> StrategyState | None:
        """Read+decode state. Caller must hold ``_locked`` when the file may change."""
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise
        return self._decode_payload(payload)

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
        with self._locked():
            disk = self._read_unlocked()
            if disk is not None:
                merged = merge_concurrent_group_updates(state, disk)
                if merged:
                    LOGGER.info(
                        "state save merged concurrent journal updates for groups=%s path=%s",
                        ",".join(merged),
                        self.path,
                    )
            serialized = json.dumps(
                state.to_dict(),
                default=json_default,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
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
