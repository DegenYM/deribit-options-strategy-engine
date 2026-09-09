"""Durable atomic file writes shared by state / heartbeat / archive writers.

``Path.write_text`` + ``os.replace`` is atomic with respect to *readers* (they
never see a half-written file) but not with respect to *power loss*: the rename
can be journaled before the new file's data blocks reach the disk, leaving an
empty or truncated state file after a crash. Every writer here therefore
``fsync``s the temp file before the rename and then (best-effort) the parent
directory so the rename itself is durable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory so a completed rename/append is durable.

    Some platforms/filesystems (notably macOS on APFS, or FAT/SMB mounts)
    refuse ``fsync`` on directory descriptors; that is logged at debug level
    and ignored rather than failing the write.
    """
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        LOGGER.debug("directory fsync skipped (open failed) for %s: %s", directory, exc)
        return
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        LOGGER.debug("directory fsync unsupported for %s: %s", directory, exc)
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8", tmp_path: Path | None = None) -> None:
    """Write ``text`` to ``path`` atomically and durably.

    Steps: write to ``tmp_path`` (default ``<path>.tmp``), ``flush`` + ``fsync``
    the file, close it, ``os.replace`` onto ``path``, then fsync the parent
    directory. On any failure the temp file is removed and the original ``path``
    is left untouched.
    """
    target = Path(path)
    tmp = Path(tmp_path) if tmp_path is not None else target.with_suffix(target.suffix + ".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    fsync_directory(target.parent)


def durable_append_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Append ``text`` to ``path`` and fsync the file (plus parent dir on create).

    Used by append-only journals (e.g. the closed-group archive). A single
    ``write`` call of a moderately sized buffer opened in append mode is atomic
    on POSIX for local filesystems, so concurrent appenders do not interleave
    within a line; the fsync makes the appended record durable.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    with open(target, "a", encoding=encoding) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if not existed:
        fsync_directory(target.parent)
