from __future__ import annotations

import os
from pathlib import Path

import pytest

from deribit_engine import atomic_io
from deribit_engine.atomic_io import atomic_write_text, durable_append_text


def _count_fsync(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real_fsync = os.fsync

    def _fake(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(atomic_io.os, "fsync", _fake)
    return calls


def test_atomic_write_text_fsyncs_file_and_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_fsync(monkeypatch)
    target = tmp_path / "nested" / "state.json"

    atomic_write_text(target, '{"a":1}')

    assert target.read_text(encoding="utf-8") == '{"a":1}'
    assert not (tmp_path / "nested" / "state.json.tmp").exists()
    # One fsync for the temp file, one (best-effort) for the parent directory.
    assert len(calls) == 2


def test_atomic_write_text_directory_fsync_failure_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_fsync = os.fsync
    seen: list[int] = []

    def _fake(fd: int) -> None:
        seen.append(fd)
        if len(seen) == 2:
            raise OSError("EINVAL: directory fsync unsupported")
        real_fsync(fd)

    monkeypatch.setattr(atomic_io.os, "fsync", _fake)
    target = tmp_path / "state.json"
    atomic_write_text(target, "ok")
    assert target.read_text() == "ok"
    assert len(seen) == 2


def test_atomic_write_text_failure_leaves_original_and_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, "first")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_io.os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "second")

    assert target.read_text() == "first"
    assert not (tmp_path / "state.json.tmp").exists()


def test_atomic_write_text_custom_tmp_path(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    tmp = tmp_path / "state.json.custom-tmp"
    atomic_write_text(target, "x", tmp_path=tmp)
    assert target.read_text() == "x"
    assert not tmp.exists()


def test_durable_append_text_appends_and_fsyncs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_fsync(monkeypatch)
    target = tmp_path / "archive.jsonl"

    durable_append_text(target, "a\n")
    durable_append_text(target, "b\n")

    assert target.read_text().splitlines() == ["a", "b"]
    # First append: file + directory (new file). Second: file only.
    assert len(calls) == 3
