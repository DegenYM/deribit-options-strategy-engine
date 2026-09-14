from pathlib import Path
from unittest.mock import patch

from deribit_engine.admin_launchd import (
    ADMIN_LAUNCHD_LABEL,
    install_admin_plist,
    manage_admin_launchd,
    probe_admin_health,
    wait_for_admin_health,
)


def test_install_admin_plist_writes_launchagent(tmp_path: Path) -> None:
    template_src = Path(__file__).resolve().parents[1] / "config" / "launchd" / "com.deribit.admin.plist.template"
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    (tmp_path / "config" / "launchd" / "com.deribit.admin.plist.template").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    with (
        patch("deribit_engine.admin_launchd.launch_agents_dir", return_value=agents),
        patch(
            "deribit_engine.admin_launchd.admin_log_paths",
            return_value=(tmp_path / "admin.log", tmp_path / "admin.err.log"),
        ),
    ):
        dest, changed = install_admin_plist(repo_root=tmp_path, python_bin="/opt/crypto/bin/python")

    assert changed is True
    assert dest.name == "com.deribit.admin.plist"
    text = dest.read_text(encoding="utf-8")
    assert ADMIN_LAUNCHD_LABEL in text
    assert "/opt/crypto/bin/python" in text
    assert f"{tmp_path}/bot" in text
    assert "admin" in text
    assert "8750" in text


def test_manage_admin_start_bootstraps(tmp_path: Path) -> None:
    template_src = Path(__file__).resolve().parents[1] / "config" / "launchd" / "com.deribit.admin.plist.template"
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    (tmp_path / "config" / "launchd" / "com.deribit.admin.plist.template").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "/opt/crypto/bin/python"',
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
        patch("deribit_engine.admin_launchd.launch_agents_dir", return_value=agents),
        patch(
            "deribit_engine.admin_launchd.admin_log_paths",
            return_value=(tmp_path / "admin.log", tmp_path / "admin.err.log"),
        ),
        patch("deribit_engine.investor_launchd_common.subprocess.run", side_effect=fake_run),
        patch("deribit_engine.admin_launchd.wait_for_admin_health", return_value=True),
    ):
        result = manage_admin_launchd("start", repo_root=tmp_path, check_health=True)

    assert result.ok is True
    assert result.state == "healthy"
    assert (agents / "com.deribit.admin.plist").is_file()


def test_wait_for_admin_health_retries() -> None:
    calls = {"n": 0}

    def fake_probe(**kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    with patch("deribit_engine.admin_launchd.probe_admin_health", side_effect=fake_probe):
        assert wait_for_admin_health(max_wait_sec=2.0, poll_interval_sec=0.01) is True
    assert calls["n"] == 2


def test_probe_admin_health_uses_local_url() -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("deribit_engine.admin_launchd.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
        assert probe_admin_health() is True
    assert urlopen.call_args.args[0] == "http://127.0.0.1:8750/api/admin/health"
