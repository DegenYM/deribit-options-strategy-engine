import signal
from pathlib import Path
from unittest.mock import patch

from deribit_engine.investor_launchd_common import (
    bootout_plist,
    bootstrap_plist,
    enable_launchd,
    force_reload_plist,
    list_pids_listening_on_tcp_port,
    terminate_tcp_listeners,
)


def test_list_pids_listening_on_tcp_port_parses_lsof_output():
    class Result:
        returncode = 0
        stdout = "12345\n67890\n"
        stderr = ""

    with patch("deribit_engine.investor_launchd_common.subprocess.run", return_value=Result()):
        assert list_pids_listening_on_tcp_port(8765) == [12345, 67890]


def test_terminate_tcp_listeners_sends_sigterm_when_process_exits():
    calls: list[tuple[int, int]] = []
    list_calls = {"n": 0}

    def fake_list(port: int) -> list[int]:
        list_calls["n"] += 1
        return [4242] if list_calls["n"] == 1 else []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    with (
        patch(
            "deribit_engine.investor_launchd_common.list_pids_listening_on_tcp_port",
            side_effect=fake_list,
        ),
        patch("deribit_engine.investor_launchd_common.os.kill", side_effect=fake_kill),
        patch("deribit_engine.investor_launchd_common.time.sleep", return_value=None),
    ):
        killed, msg = terminate_tcp_listeners(8765, grace_sec=0.0)

    assert killed == [4242]
    assert "terminated" in msg
    assert calls == [(4242, signal.SIGTERM)]


def test_force_reload_plist_bootouts_kills_and_bootstraps(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    steps: list[str] = []

    def fake_loaded(label: str) -> bool:
        return label == "com.deribit.frontend.alice"

    def fake_bootout(path: Path, label: str) -> tuple[bool, str]:
        steps.append("bootout")
        return True, "stopped"

    def fake_terminate(port: int | None, **kwargs) -> tuple[list[int], str]:
        steps.append(f"kill:{port}")
        return [999], "terminated [999]"

    def fake_bootstrap(path: Path, label: str | None = None) -> tuple[bool, str]:
        steps.append("bootstrap")
        return True, "bootstrapped"

    with (
        patch("deribit_engine.investor_launchd_common.is_launchd_loaded", side_effect=fake_loaded),
        patch("deribit_engine.investor_launchd_common.bootout_plist", side_effect=fake_bootout),
        patch("deribit_engine.investor_launchd_common.terminate_tcp_listeners", side_effect=fake_terminate),
        patch("deribit_engine.investor_launchd_common.bootstrap_plist", side_effect=fake_bootstrap),
    ):
        ok, msg = force_reload_plist(
            plist,
            "com.deribit.frontend.alice",
            listen_port=8800,
        )

    assert ok is True
    assert steps == ["bootout", "kill:8800", "bootstrap"]
    assert "force reloaded" in msg


def test_manage_frontend_restart_uses_force_reload(tmp_path: Path):
    from deribit_engine.investor_frontend_launchd import manage_frontend_launchd
    from deribit_engine.investor_registry import InvestorRegistryEntry

    def _entry(investor_id: str, *, port: int) -> InvestorRegistryEntry:
        return InvestorRegistryEntry(
            investor_id=investor_id,
            display_name=investor_id,
            dashboard_email=None,
            access_method="email",
            hostname=f"{investor_id}.portfolio.test",
            frontend_port=port,
            live_enabled=True,
            frontend_enabled=True,
        )

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
            ]
        ),
        encoding="utf-8",
    )

    agents = tmp_path / "LaunchAgents"
    agents.mkdir()

    with (
        patch("deribit_engine.investor_frontend_launchd.launch_agents_dir", return_value=agents),
        patch(
            "deribit_engine.investor_frontend_launchd._force_reload_frontend_plist",
            return_value=(True, "force reloaded (bootout: stopped; kill: terminated [1]; bootstrap: bootstrapped)"),
        ) as force_reload,
        patch("deribit_engine.investor_frontend_launchd.is_frontend_loaded", return_value=True),
        patch("deribit_engine.investor_frontend_launchd.wait_for_frontend_health", return_value=True),
    ):
        results = manage_frontend_launchd(
            "restart",
            repo_root=tmp_path,
            investor_id="alice",
            check_health=True,
        )

    assert len(results) == 1
    assert results[0].ok is True
    assert "force reloaded" in results[0].message
    force_reload.assert_called_once()
    assert force_reload.call_args.kwargs["listen_port"] == 8800


def _launchctl_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    class Result:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return Result()


def _patch_uid():
    return patch("deribit_engine.investor_launchd_common.os.getuid", return_value=501)


def test_bootstrap_plist_enables_before_bootstrap(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _launchctl_result(0)

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootstrap_plist(plist)

    assert ok is True
    assert msg == "bootstrapped"
    assert calls == [
        ["launchctl", "enable", "gui/501/com.deribit.frontend.alice"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]


def test_bootstrap_plist_aborts_when_enable_fails(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "enable"]:
            return _launchctl_result(1, stderr="enable denied")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootstrap_plist(plist, label="com.deribit.frontend.alice")

    assert ok is False
    assert msg == "enable failed: enable denied"
    assert calls == [["launchctl", "enable", "gui/501/com.deribit.frontend.alice"]]


def test_bootstrap_input_output_error_succeeds_only_if_print_shows_loaded(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "enable"]:
            return _launchctl_result(0)
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return _launchctl_result(5, stderr="Bootstrap failed: 5: Input/output error")
        if cmd[:2] == ["launchctl", "print"]:
            return _launchctl_result(0, stdout="state = running\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootstrap_plist(plist)

    assert ok is True
    assert msg == "already loaded"
    assert calls[-1] == ["launchctl", "print", "gui/501/com.deribit.frontend.alice"]


def test_bootstrap_input_output_error_fails_if_print_shows_not_loaded(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["launchctl", "enable"]:
            return _launchctl_result(0)
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return _launchctl_result(5, stderr="Bootstrap failed: 5: Input/output error")
        if cmd[:2] == ["launchctl", "print"]:
            return _launchctl_result(1, stderr="Could not find service")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootstrap_plist(plist)

    assert ok is False
    assert "Input/output error" in msg


def test_bootout_success_reenables_and_does_not_unload(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _launchctl_result(0)

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootout_plist(plist, "com.deribit.frontend.alice")

    assert ok is True
    assert msg == "stopped"
    assert calls == [
        ["launchctl", "bootout", "gui/501", str(plist)],
        ["launchctl", "enable", "gui/501/com.deribit.frontend.alice"],
    ]
    assert all(cmd[1] != "unload" for cmd in calls)


def test_bootout_not_loaded_is_success_without_unload(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "bootout"]:
            return _launchctl_result(1, stderr="Could not find specified service")
        if cmd[:2] == ["launchctl", "print"]:
            return _launchctl_result(1, stderr="Could not find service")
        if cmd[:2] == ["launchctl", "enable"]:
            return _launchctl_result(0)
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootout_plist(plist, "com.deribit.frontend.alice")

    assert ok is True
    assert msg == "not loaded"
    assert ["launchctl", "enable", "gui/501/com.deribit.frontend.alice"] in calls
    assert all(cmd[1] != "unload" for cmd in calls)


def test_bootout_failure_when_still_loaded_does_not_unload(tmp_path: Path):
    plist = tmp_path / "com.deribit.frontend.alice.plist"
    plist.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "bootout"]:
            return _launchctl_result(1, stderr="Boot-out failed: 5: Input/output error")
        if cmd[:2] == ["launchctl", "print"]:
            return _launchctl_result(0, stdout="state = running\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = bootout_plist(plist, "com.deribit.frontend.alice")

    assert ok is False
    assert "Input/output error" in msg
    assert all(cmd[1] != "unload" for cmd in calls)
    assert all(cmd[1] != "enable" for cmd in calls)


def test_enable_launchd_invokes_gui_domain_target():
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _launchctl_result(0)

    with (
        _patch_uid(),
        patch(
            "deribit_engine.investor_launchd_common.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        ok, msg = enable_launchd("com.deribit.admin")

    assert ok is True
    assert msg == "enabled"
    assert calls == [["launchctl", "enable", "gui/501/com.deribit.admin"]]
