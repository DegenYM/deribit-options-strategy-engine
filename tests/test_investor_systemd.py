from pathlib import Path

import pytest

from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_ops import investor_init, render_systemd_units
from deribit_engine.investor_registry import load_platform_registry


def _bootstrap_repo(tmp_path: Path) -> Path:
    (tmp_path / "deribit_engine").mkdir()
    example = Path(__file__).resolve().parents[1] / "config" / "investors" / "_example"
    (tmp_path / "config" / "investors" / "_example").mkdir(parents=True)
    for rel in (
        "accounts.toml",
        ".env.investor.example",
        "accounts/.env.naked.example",
    ):
        src = example / rel
        dest = tmp_path / "config" / "investors" / "_example" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "platform").mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    for subdir, names in (
        ("launchd", ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template")),
        ("systemd", ("com.deribit.live.service.template", "com.deribit.frontend.service.template")),
    ):
        (tmp_path / "config" / subdir).mkdir(parents=True)
        for name in names:
            src = repo_root / "config" / subdir / name
            (tmp_path / "config" / subdir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "/usr/bin/python3"',
                'domain = "portfolio.test"',
                "next_frontend_port = 8800",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_render_systemd_units_writes_live_and_frontend(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    registry = load_platform_registry(repo_root=repo)
    paths = render_systemd_units("alice", repo_root=repo, registry=registry, frontend_port=8810)
    assert len(paths) == 2
    live_path = repo / "config/platform/generated/systemd/com.deribit.live.alice.service"
    frontend_path = repo / "config/platform/generated/systemd/com.deribit.frontend.alice.service"
    assert paths == (live_path, frontend_path)
    live_text = live_path.read_text(encoding="utf-8")
    frontend_text = frontend_path.read_text(encoding="utf-8")
    assert str(repo) in live_text
    assert "/usr/bin/python3" in live_text
    assert "--investor alice" in live_text
    assert "Restart=always" in live_text
    assert "--port 8810" in frontend_text


def test_investor_init_writes_systemd_units(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    result = investor_init("bob", strategies=("naked",), register=False, repo_root=repo)
    assert len(result.systemd_paths) == 2
    assert (repo / "config/platform/generated/systemd/com.deribit.live.bob.service").is_file()


def test_render_systemd_units_missing_template_raises(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    (repo / "config/systemd/com.deribit.live.service.template").unlink()
    with pytest.raises(ConfigurationError, match="Missing template"):
        render_systemd_units("alice", repo_root=repo)
