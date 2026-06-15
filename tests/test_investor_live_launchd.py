from pathlib import Path
from unittest.mock import patch

import pytest

from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_live_launchd import (
    install_live_plist,
    live_launchd_label,
    live_targets,
    manage_live_launchd,
    probe_live_supervisor,
    wait_for_live_supervisor,
)
from deribit_engine.investor_registry import (
    InvestorRegistryEntry,
    PlatformRegistry,
    PlatformSettings,
)


def _registry(tmp_path: Path, *, investors: list[InvestorRegistryEntry]) -> PlatformRegistry:
    return PlatformRegistry(
        path=tmp_path / "config/platform/registry.toml",
        platform=PlatformSettings(
            repo_root=tmp_path,
            python_bin="python3",
            domain="portfolio.test",
            tunnel_name=None,
            next_frontend_port=8800,
        ),
        investors=tuple(investors),
    )


def _entry(
    investor_id: str,
    *,
    live_enabled: bool = True,
) -> InvestorRegistryEntry:
    return InvestorRegistryEntry(
        investor_id=investor_id,
        display_name=investor_id,
        dashboard_email=None,
        access_method="email",
        hostname=f"{investor_id}.portfolio.test",
        frontend_port=8800,
        live_enabled=live_enabled,
        frontend_enabled=True,
    )


def test_live_targets_filters_disabled():
    registry = _registry(
        Path("/tmp"),
        investors=[
            _entry("alice", live_enabled=True),
            _entry("bob", live_enabled=False),
        ],
    )
    rows = live_targets(registry)
    assert [row.investor_id for row in rows] == ["alice"]

    with pytest.raises(ConfigurationError):
        live_targets(registry, investor_id="bob")


def test_live_label():
    assert live_launchd_label("Pat") == "com.deribit.live.pat"


def test_install_live_plist_copies_generated(tmp_path: Path):
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    for name in ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "launchd" / name
        (tmp_path / "config" / "launchd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    registry = _registry(tmp_path, investors=[_entry("alice")])
    with patch(
        "deribit_engine.investor_live_launchd.launch_agents_dir",
        return_value=tmp_path / "LaunchAgents",
    ):
        dest, changed = install_live_plist("alice", repo_root=tmp_path, registry=registry)
    assert dest.name == "com.deribit.live.alice.plist"
    assert changed is True
    assert "run_live_profiles.py" in dest.read_text(encoding="utf-8")


def test_manage_start_bootstraps_each_investor(tmp_path: Path):
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    for name in ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "launchd" / name
        (tmp_path / "config" / "launchd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "python3"',
                "",
                "[[investors]]",
                'id = "alice"',
                'display_name = "Alice"',
                "live_enabled = true",
                "",
                "[[investors]]",
                'id = "bob"',
                'display_name = "Bob"',
                "live_enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    agents = tmp_path / "LaunchAgents"
    agents.mkdir()

    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[:2] == ["launchctl", "print"]:
            return Result()
        return Result()

    with (
        patch("deribit_engine.investor_live_launchd.launch_agents_dir", return_value=agents),
        patch("deribit_engine.investor_launchd_common.subprocess.run", side_effect=fake_run),
        patch(
            "deribit_engine.investor_live_launchd.wait_for_live_supervisor",
            return_value=True,
        ),
    ):
        results = manage_live_launchd("start", repo_root=tmp_path, check_supervisor=True)

    assert len(results) == 2
    assert all(row.ok for row in results)
    assert (agents / "com.deribit.live.alice.plist").is_file()
    assert (agents / "com.deribit.live.bob.plist").is_file()


def test_probe_live_supervisor_reads_log(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "deribit_engine.investor_launchd_common.Path.home",
        classmethod(lambda cls: tmp_path),
    )
    from deribit_engine.investor_launchd_common import investor_live_launchd_log_paths

    stdout, _ = investor_live_launchd_log_paths("alice")
    stdout.parent.mkdir(parents=True, exist_ok=True)
    stdout.write_text("started .env.naked pid=123 log=...\n", encoding="utf-8")
    assert probe_live_supervisor(tmp_path, "alice") is True


def test_wait_for_live_supervisor_retries(tmp_path: Path):
    log_dir = tmp_path / "logs" / "live" / "alice"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "supervisor.log"
    calls = {"n": 0}

    def fake_probe(repo_root: Path, investor_id: str) -> bool:
        calls["n"] += 1
        if calls["n"] >= 2:
            log_path.write_text("started .env.naked pid=1\n", encoding="utf-8")
            return True
        return False

    with patch("deribit_engine.investor_live_launchd.probe_live_supervisor", side_effect=fake_probe):
        assert wait_for_live_supervisor(tmp_path, "alice", max_wait_sec=2.0, poll_interval_sec=0.01) is True
