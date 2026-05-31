from pathlib import Path
from unittest.mock import patch

from deribit_engine.cloudflared_launchd import (
    install_tunnel_plist,
    manage_tunnel_launchd,
    probe_tunnel_metrics,
    tunnel_launchd_label,
    tunnel_slug,
    wait_for_tunnel_metrics,
)
from deribit_engine.investor_registry import PlatformRegistry, PlatformSettings


def _registry(tmp_path: Path, *, tunnel_name: str = "debopt-jack") -> PlatformRegistry:
    return PlatformRegistry(
        path=tmp_path / "config/platform/registry.toml",
        platform=PlatformSettings(
            repo_root=tmp_path,
            python_bin="python3",
            domain="portfolio.test",
            tunnel_name=tunnel_name,
            next_frontend_port=8800,
        ),
        investors=(),
    )


def test_tunnel_slug():
    assert tunnel_slug("debopt-jack") == "debopt-jack"
    assert tunnel_slug("Deribit Tunnel") == "deribit-tunnel"
    assert tunnel_launchd_label("debopt-jack") == "com.deribit.cloudflared.debopt-jack"


def test_install_tunnel_plist_writes_launchagent(tmp_path: Path):
    config_dir = tmp_path / ".cloudflared"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("tunnel: test\n", encoding="utf-8")

    template_src = Path(__file__).resolve().parents[1] / "config" / "launchd" / "com.deribit.cloudflared.plist.template"
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    (tmp_path / "config" / "launchd" / "com.deribit.cloudflared.plist.template").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    registry = _registry(tmp_path)
    with (
        patch("deribit_engine.cloudflared_launchd.cloudflared_config_path", return_value=config_dir / "config.yml"),
        patch(
            "deribit_engine.cloudflared_launchd.resolve_cloudflared_bin", return_value="/opt/homebrew/bin/cloudflared"
        ),
        patch("deribit_engine.cloudflared_launchd.launch_agents_dir", return_value=tmp_path / "LaunchAgents"),
        patch(
            "deribit_engine.cloudflared_launchd.tunnel_log_paths",
            return_value=(tmp_path / "cloudflared.log", tmp_path / "cloudflared.err.log"),
        ),
    ):
        dest, changed = install_tunnel_plist(repo_root=tmp_path, registry=registry)

    assert changed is True
    assert dest.name == "com.deribit.cloudflared.debopt-jack.plist"
    text = dest.read_text(encoding="utf-8")
    assert "/opt/homebrew/bin/cloudflared" in text
    assert "config.yml" in text


def test_manage_tunnel_start_bootstraps(tmp_path: Path):
    config_dir = tmp_path / ".cloudflared"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("tunnel: test\n", encoding="utf-8")

    template_src = Path(__file__).resolve().parents[1] / "config" / "launchd" / "com.deribit.cloudflared.plist.template"
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    (tmp_path / "config" / "launchd" / "com.deribit.cloudflared.plist.template").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "python3"',
                'tunnel_name = "debopt-jack"',
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
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        return Result()

    with (
        patch("deribit_engine.cloudflared_launchd.cloudflared_config_path", return_value=config_dir / "config.yml"),
        patch(
            "deribit_engine.cloudflared_launchd.resolve_cloudflared_bin", return_value="/opt/homebrew/bin/cloudflared"
        ),
        patch("deribit_engine.cloudflared_launchd.launch_agents_dir", return_value=agents),
        patch(
            "deribit_engine.cloudflared_launchd.tunnel_log_paths",
            return_value=(tmp_path / "cloudflared.log", tmp_path / "cloudflared.err.log"),
        ),
        patch("deribit_engine.investor_launchd_common.subprocess.run", side_effect=fake_run),
        patch("deribit_engine.cloudflared_launchd.wait_for_tunnel_metrics", return_value=True),
    ):
        result = manage_tunnel_launchd("start", repo_root=tmp_path, check_health=True)

    assert result.ok is True
    assert result.state == "healthy"
    assert (agents / "com.deribit.cloudflared.debopt-jack.plist").is_file()


def test_manage_tunnel_missing_config_raises(tmp_path: Path):
    template_src = Path(__file__).resolve().parents[1] / "config" / "launchd" / "com.deribit.cloudflared.plist.template"
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    (tmp_path / "config" / "launchd" / "com.deribit.cloudflared.plist.template").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "python3"',
                'tunnel_name = "debopt-jack"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with (
        patch("deribit_engine.cloudflared_launchd.cloudflared_config_path", return_value=tmp_path / "missing.yml"),
        patch(
            "deribit_engine.cloudflared_launchd.resolve_cloudflared_bin", return_value="/opt/homebrew/bin/cloudflared"
        ),
    ):
        result = manage_tunnel_launchd("start", repo_root=tmp_path, check_health=False)

    assert result.ok is False
    assert "Missing cloudflared config" in result.message


def test_wait_for_tunnel_metrics_retries():
    calls = {"n": 0}

    def fake_probe(**kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    with patch("deribit_engine.cloudflared_launchd.probe_tunnel_metrics", side_effect=fake_probe):
        assert wait_for_tunnel_metrics(max_wait_sec=2.0, poll_interval_sec=0.01) is True
    assert calls["n"] == 2


def test_probe_tunnel_metrics_uses_metrics_url():
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("deribit_engine.cloudflared_launchd.urllib.request.urlopen", return_value=FakeResponse()):
        assert probe_tunnel_metrics() is True
