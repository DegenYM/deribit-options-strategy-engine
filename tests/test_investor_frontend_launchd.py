from pathlib import Path
from unittest.mock import patch

import pytest

from deribit_demo.exceptions import ConfigurationError
from deribit_demo.investor_frontend_launchd import (
    frontend_launchd_label,
    frontend_plist_filename,
    frontend_targets,
    install_frontend_plist,
    manage_frontend_launchd,
    probe_frontend_health,
    read_frontend_port_from_plist,
    wait_for_frontend_health,
)
from deribit_demo.investor_registry import (
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
    port: int,
    frontend_enabled: bool = True,
) -> InvestorRegistryEntry:
    return InvestorRegistryEntry(
        investor_id=investor_id,
        display_name=investor_id,
        dashboard_email=None,
        access_method="email",
        hostname=f"{investor_id}.portfolio.test",
        frontend_port=port,
        live_enabled=True,
        frontend_enabled=frontend_enabled,
    )


def test_frontend_targets_filters_disabled():
    registry = _registry(
        Path("/tmp"),
        investors=[
            _entry("alice", port=8800, frontend_enabled=True),
            _entry("bob", port=8801, frontend_enabled=False),
        ],
    )
    rows = frontend_targets(registry)
    assert [row.investor_id for row in rows] == ["alice"]

    with pytest.raises(ConfigurationError):
        frontend_targets(registry, investor_id="bob")


def test_frontend_label_and_plist_names():
    assert frontend_launchd_label("Pat") == "com.deribit.frontend.pat"
    assert frontend_plist_filename("jack") == "com.deribit.frontend.jack.plist"


def test_install_frontend_plist_copies_generated(tmp_path: Path):
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    for name in ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "launchd" / name
        (tmp_path / "config" / "launchd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    registry = _registry(tmp_path, investors=[_entry("alice", port=8800)])
    with patch(
        "deribit_demo.investor_frontend_launchd.launch_agents_dir",
        return_value=tmp_path / "LaunchAgents",
    ):
        dest, changed = install_frontend_plist("alice", repo_root=tmp_path, registry=registry)
    assert dest.name == "com.deribit.frontend.alice.plist"
    assert changed is True
    assert read_frontend_port_from_plist(dest) == 8800
    assert "8800" in dest.read_text(encoding="utf-8")


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
                "frontend_port = 8800",
                "frontend_enabled = true",
                "",
                "[[investors]]",
                'id = "bob"',
                'display_name = "Bob"',
                "frontend_port = 8801",
                "frontend_enabled = true",
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
        patch("deribit_demo.investor_frontend_launchd.launch_agents_dir", return_value=agents),
        patch("deribit_demo.investor_launchd_common.subprocess.run", side_effect=fake_run),
        patch("deribit_demo.investor_frontend_launchd.wait_for_frontend_health", return_value=True),
    ):
        results = manage_frontend_launchd(
            "start",
            repo_root=tmp_path,
            check_health=True,
        )

    assert len(results) == 2
    assert all(row.ok for row in results)
    assert (agents / "com.deribit.frontend.alice.plist").is_file()
    assert (agents / "com.deribit.frontend.bob.plist").is_file()


def test_wait_for_frontend_health_retries(tmp_path: Path):
    calls = {"n": 0}

    def fake_probe(port: int, **kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    with patch(
        "deribit_demo.investor_frontend_launchd.probe_frontend_health",
        side_effect=fake_probe,
    ):
        assert wait_for_frontend_health(8765, max_wait_sec=2.0, poll_interval_sec=0.01) is True
    assert calls["n"] == 2


def test_probe_frontend_health_uses_local_url():
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("deribit_demo.investor_frontend_launchd.urllib.request.urlopen", return_value=FakeResponse()):
        assert probe_frontend_health(8765) is True
