from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from .atomic_io import atomic_write_text, durable_append_text
from .env_parse import parse_env_bool
from .models import StrategyState, TradeGroup
from .utils import json_default, utc_now_ms

try:
    import fcntl
except ImportError:  # pragma: no cover — POSIX only; Windows not supported by plan.
    fcntl = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

# Closed-group archival defaults (see ``StrategyStateStore.archive_closed_groups``).
DEFAULT_CLOSED_ARCHIVE_KEEP_DAYS = 90
DEFAULT_CLOSED_ARCHIVE_KEEP_MIN = 20
_MS_PER_DAY = 86_400_000


def state_json_pretty_from_environ() -> bool:
    """``STATE_JSON_PRETTY=true`` restores indented state files (default compact)."""
    return bool(parse_env_bool(os.environ.get("STATE_JSON_PRETTY"), default=False, strict=False))


def serialize_state(state: StrategyState, *, pretty: bool = False) -> str:
    """Deterministic JSON for a state file.

    Compact by default: the file is rewritten every live cycle and closed groups
    accumulate, so indentation roughly doubles the bytes written per save.
    ``sort_keys`` is kept in both modes so diffs / fingerprints stay stable.
    """
    if pretty:
        return json.dumps(state.to_dict(), default=json_default, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(
        state.to_dict(),
        default=json_default,
        ensure_ascii=False,
        indent=None,
        separators=(",", ":"),
        sort_keys=True,
    )


# Live manage cycles load state at start and save at end. Concurrent CLI tools
# (spot-restore / profit-sweep / …) may update the same file mid-cycle; a naive
# save would clobber those fields. On save we re-read disk under the lock and
# merge "operator journal" clusters when disk is ahead.
_JOURNAL_STATUS_RANK = {
    "": 0,
    "pending": 1,
    "submitted": 2,
    "failed": 2,
    "entered": 3,
    "filled": 3,
    # Operator terminal skip must outrank in-flight pending/failed so a live
    # cycle cannot merge the old sell back (Youming #0082 manual withdrawal).
    "skipped": 4,
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
_CASH_SECURED_FIELDS = (
    "cash_secured_status",
    "cash_secured_group_id",
    "cash_secured_group_ids",
    "cash_secured_reason",
    "cash_secured_order_id",
    "cash_secured_instrument_name",
    "cash_secured_limit_price",
    "cash_secured_from_group_id",
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


_CLOSED_ARCHIVE_SUFFIX = ".closed_archive.jsonl"


def closed_archive_path(state_path: Path) -> Path:
    """``<state stem>.closed_archive.jsonl`` next to the state file."""
    if state_path.name.endswith(_CLOSED_ARCHIVE_SUFFIX):
        return state_path
    return state_path.with_name(f"{state_path.stem}{_CLOSED_ARCHIVE_SUFFIX}")


def load_archived_groups(path: Path) -> list[TradeGroup]:
    """Read every archived closed group (append-only JSONL, one ``TradeGroup`` per line).

    ``path`` may be the state file or the archive file itself. Corrupt lines
    (e.g. a torn write from a crash mid-append) are skipped with a warning so
    one bad record never hides the rest of the history.
    """
    archive = closed_archive_path(Path(path))
    if not archive.is_file():
        return []
    groups: list[TradeGroup] = []
    seen: set[str] = set()
    try:
        raw = archive.read_text(encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("failed to read closed-group archive %s: %s", archive, exc)
        return []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
            group = TradeGroup.from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — one bad line must not hide the archive.
            LOGGER.warning("skipping corrupt archive line %s in %s: %s", line_no, archive, exc)
            continue
        gid = str(group.group_id or "")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        groups.append(group)
    return groups


def iter_all_groups(state: StrategyState, path: Path) -> Iterator[TradeGroup]:
    """Yield live groups followed by archived closed groups (deduped by ``group_id``).

    Readers that need full history (fee reports, realized summaries, dashboard
    closed tables) must use this instead of ``state.groups`` once archival is
    enabled. Live copies win over archived copies with the same id.
    """
    seen: set[str] = set()
    for group in state.groups:
        gid = str(group.group_id or "")
        if gid:
            seen.add(gid)
        yield group
    for group in load_archived_groups(path):
        gid = str(group.group_id or "")
        if gid and gid in seen:
            continue
        yield group


# Follow-up journals a *closed* group may still be driving. A status outside the
# terminal set means the live cycle / an operator CLI still has work to do on the
# group (queue, poll or retry an order), so it must stay in ``state.groups``:
# archived groups are invisible to those loops and the operation would be
# orphaned. Unknown values are treated as non-terminal (conservative).
#
# Terminal sets are derived from the writers:
# - profit sweep: ``filled`` / ``skipped`` are done; ``pending`` / ``submitted``
#   are in flight and ``failed`` is re-queued by
#   ``profit_sweep_ops.reschedule_failed_profit_sweeps`` (so it is *not* terminal).
# - spot restore: ``filled`` / ``skipped`` (``spot_restore_ops.mark_spot_restore_
#   operator_cancelled`` also accepts ``cancelled`` / ``canceled``); ``pending`` /
#   ``submitted`` are in flight.
# - CSP premium swap: ``csp_premium_swap_ops.CSP_PREMIUM_SWAP_TERMINAL``
#   (``filled`` / ``skipped``); ``pending`` / ``submitted`` are in flight.
# - ITM spot exit: ``filled`` / ``skipped`` are done; ``pending`` / ``submitted``
#   are in flight and ``failed`` is re-queued (``engine/execution.py``).
# - cash-secured wheel (parent side): ``entered`` / ``skipped`` are done;
#   ``pending`` / ``submitted`` mean the child put is still being opened.
_FOLLOW_UP_TERMINAL_STATUSES: dict[str, frozenset[str]] = {
    "profit_sweep_status": frozenset({"", "filled", "skipped"}),
    "spot_restore_status": frozenset({"", "filled", "skipped", "cancelled", "canceled"}),
    "csp_premium_swap_status": frozenset({"", "filled", "skipped"}),
    "spot_exit_status": frozenset({"", "filled", "skipped"}),
    "cash_secured_status": frozenset({"", "entered", "skipped"}),
}


def group_has_pending_follow_up(group: TradeGroup) -> bool:
    """True when any follow-up journal on ``group`` is not in a terminal status."""
    for attr, terminal in _FOLLOW_UP_TERMINAL_STATUSES.items():
        status = str(getattr(group, attr, "") or "").strip().lower()
        if status not in terminal:
            return True
    return False


def referenced_parent_group_ids(groups: list[TradeGroup]) -> set[str]:
    """Ids of wheel parents that some group still in ``groups`` points at.

    A cash-secured child links to the covered call that funded it through
    ``cash_secured_from_group_id``; wheel helpers (``cash_secured_ops``) resolve
    that link against the live group list, so the parent must stay until every
    child has left ``state.groups`` (archived or removed).
    """
    return {
        str(g.cash_secured_from_group_id or "").strip()
        for g in groups
        if str(g.cash_secured_from_group_id or "").strip()
    }


def closed_group_is_archivable(
    group: TradeGroup,
    *,
    groups: list[TradeGroup],
    referenced_parent_ids: set[str] | None = None,
) -> bool:
    """Pure eligibility check for moving a closed group into the archive.

    A closed group is archivable only when it has no pending follow-up work
    (see ``group_has_pending_follow_up``) and no other group in ``groups``
    references it as its wheel parent. Only parents are protected: a child
    whose parent is retained may be archived on its own.

    Performance-exclusion ids (``load_performance_exclusion_group_ids``) are
    deliberately *not* consulted: every reader simply skips those ids, and the
    archive keeps the group readable via ``iter_all_groups``, so archiving an
    excluded group changes nothing for reports.
    """
    if str(group.status or "").lower() != "closed":
        return False
    if group_has_pending_follow_up(group):
        return False
    referenced = referenced_parent_ids if referenced_parent_ids is not None else referenced_parent_group_ids(groups)
    gid = str(group.group_id or "").strip()
    if gid and gid in referenced:
        return False
    return True


def select_archivable_closed_groups(
    groups: list[TradeGroup],
    *,
    keep_recent_days: int,
    keep_min: int,
    now_ms: int | None = None,
) -> list[TradeGroup]:
    """Closed groups older than ``keep_recent_days`` AND beyond the newest ``keep_min``.

    Groups without a ``closed_timestamp_ms`` are never selected (we cannot
    reason about their age). Groups with pending follow-up work, or referenced
    as wheel parent by a group still in ``groups``, are retained (see
    ``closed_group_is_archivable``). Returned oldest-first for a stable archive
    order.
    """
    now = now_ms if now_ms is not None else utc_now_ms()
    cutoff = now - max(int(keep_recent_days), 0) * _MS_PER_DAY
    closed = [g for g in groups if str(g.status or "").lower() == "closed" and g.closed_timestamp_ms is not None]
    closed.sort(key=lambda g: (int(g.closed_timestamp_ms or 0), str(g.group_id or "")), reverse=True)
    candidates = closed[max(int(keep_min), 0) :]
    referenced = referenced_parent_group_ids(groups)
    picked = [
        g
        for g in candidates
        if int(g.closed_timestamp_ms or 0) < cutoff
        and closed_group_is_archivable(g, groups=groups, referenced_parent_ids=referenced)
    ]
    picked.reverse()
    return picked


# CSP ``skipped`` (operator cancel of an old mid park) is retryable; ``entered``
# must outrank it so a live fill is not merged back to skipped on save.
_CASH_SECURED_STATUS_RANK = {
    "": 0,
    "pending": 1,
    "submitted": 2,
    "skipped": 2,
    "entered": 3,
}


def _status_rank(status: str | None, ranks: dict[str, int] | None = None) -> int:
    table = ranks if ranks is not None else _JOURNAL_STATUS_RANK
    return table.get(str(status or "").strip().lower(), 0)


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
    ranks: dict[str, int] | None = None,
) -> bool:
    """Prefer disk when it is ahead on status, or richer on amounts at same status."""
    mem_rank = _status_rank(getattr(memory, status_attr), ranks)
    disk_rank = _status_rank(getattr(disk, status_attr), ranks)
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
        touched |= _merge_journal_cluster(
            group,
            other,
            status_attr="cash_secured_status",
            fields=_CASH_SECURED_FIELDS,
            amount_attrs=(),
            ranks=_CASH_SECURED_STATUS_RANK,
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
    - durability: the tmp file is fsync'ed before the rename and the directory after it
      (see :mod:`deribit_engine.atomic_io`), so a power loss cannot leave an empty state.
    - format: compact JSON by default; ``pretty=True`` (or ``STATE_JSON_PRETTY=true``)
      restores the indented layout. ``load`` accepts either.
    """

    def __init__(self, path: Path, *, pretty: bool | None = None):
        self.path = path
        self._pretty = state_json_pretty_from_environ() if pretty is None else bool(pretty)

    @property
    def pretty(self) -> bool:
        return self._pretty

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @property
    def tmp_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".tmp")

    @property
    def archive_path(self) -> Path:
        return closed_archive_path(self.path)

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
            serialized = serialize_state(state, pretty=self._pretty)
            atomic_write_text(self.path, serialized, tmp_path=self.tmp_path)

    # ---- closed-group archival -------------------------------------------------

    def load_archived_groups(self) -> list[TradeGroup]:
        return load_archived_groups(self.archive_path)

    def iter_all_groups(self, state: StrategyState) -> Iterator[TradeGroup]:
        return iter_all_groups(state, self.path)

    def archive_closed_groups(
        self,
        state: StrategyState,
        *,
        keep_recent_days: int = DEFAULT_CLOSED_ARCHIVE_KEEP_DAYS,
        keep_min: int = DEFAULT_CLOSED_ARCHIVE_KEEP_MIN,
        now_ms: int | None = None,
    ) -> int:
        """Move old closed groups out of ``state.groups`` into the JSONL archive.

        A closed group is archived when its ``closed_timestamp_ms`` is older than
        ``keep_recent_days`` *and* it is not among the newest ``keep_min`` closed
        groups. The archive is appended (durably) *before* the groups are removed
        from ``state``; ids already present in the archive are not re-appended,
        so a crash between append and the caller's ``save`` is self-healing and a
        repeated run is idempotent. The caller must ``save(state)`` afterwards.

        Returns the number of groups removed from ``state.groups``.
        """
        picked = select_archivable_closed_groups(
            state.groups,
            keep_recent_days=keep_recent_days,
            keep_min=keep_min,
            now_ms=now_ms,
        )
        if not picked:
            return 0
        with self._locked():
            already = {str(g.group_id or "") for g in load_archived_groups(self.archive_path)}
            lines: list[str] = []
            for group in picked:
                gid = str(group.group_id or "")
                if gid and gid in already:
                    continue
                lines.append(
                    json.dumps(
                        group.to_dict(),
                        default=json_default,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            if lines:
                durable_append_text(self.archive_path, "".join(line + "\n" for line in lines))
        picked_ids = {id(group) for group in picked}
        state.groups = [g for g in state.groups if id(g) not in picked_ids]
        LOGGER.info(
            "archived %s closed groups (appended %s new) to %s; %s groups remain in state",
            len(picked),
            len(lines),
            self.archive_path,
            len(state.groups),
        )
        return len(picked)

    def _quarantine_corrupt_file(self, *, reason: str) -> Path:
        backup = self.path.with_suffix(self.path.suffix + f".corrupt.{utc_now_ms()}")
        try:
            self.path.replace(backup)
        except OSError as exc:  # pragma: no cover — best-effort.
            LOGGER.warning("unable to quarantine %s (%s): %s", self.path, reason, exc)
        return backup


def archive_closed_groups_for_state_file(
    path: Path,
    *,
    keep_recent_days: int = DEFAULT_CLOSED_ARCHIVE_KEEP_DAYS,
    keep_min: int = DEFAULT_CLOSED_ARCHIVE_KEEP_MIN,
    pretty: bool | None = None,
    now_ms: int | None = None,
) -> int:
    """Load ``path``, archive eligible closed groups, save. Returns archived count.

    Intended for an operator/maintenance run while the live bot for that state
    file is *stopped*: a running bot holds its own in-memory copy of ``groups``
    and would write the archived groups back on its next save (harmless — the
    next archive run removes them again without duplicating archive lines — but
    noisy). Wire ``StrategyStateStore.archive_closed_groups`` into the live
    cycle (after reconcile, before save) to avoid that window entirely.
    """
    store = StrategyStateStore(Path(path), pretty=pretty)
    if not store.path.exists():
        return 0
    state = store.load()
    archived = store.archive_closed_groups(
        state,
        keep_recent_days=keep_recent_days,
        keep_min=keep_min,
        now_ms=now_ms,
    )
    if archived:
        store.save(state)
    return archived
