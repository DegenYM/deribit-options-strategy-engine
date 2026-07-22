from pathlib import Path

import pytest

from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_registry import (
    InvestorRegistryEntry,
    PlatformRegistry,
    PlatformSettings,
    default_hostname,
)
from deribit_engine.tunnel_provision import (
    collect_ingress_rules,
    merge_ingress_for_single_investor,
    parse_local_tunnel_identity,
    render_local_config_yml,
    rules_to_remote_ingress,
)


def _registry(tmp_path: Path) -> PlatformRegistry:
    return PlatformRegistry(
        path=tmp_path / "config/platform/registry.toml",
        platform=PlatformSettings(
            repo_root=tmp_path,
            python_bin="python3",
            domain="debopt.com",
            hostname_template="{id}-portfolio.debopt.com",
            tunnel_name="debopt-jack",
            next_frontend_port=8800,
        ),
        investors=(
            InvestorRegistryEntry(
                investor_id="jack",
                display_name="Jack",
                dashboard_email="jack@example.com",
                access_method="email",
                hostname="jack-portfolio.debopt.com",
                frontend_port=8766,
                live_enabled=True,
                frontend_enabled=True,
            ),
            InvestorRegistryEntry(
                investor_id="eugene",
                display_name="Eugene",
                dashboard_email="eugene@example.com",
                access_method="email",
                hostname="yoeugene-portfolio.debopt.com",
                frontend_port=8771,
                live_enabled=True,
                frontend_enabled=True,
            ),
            InvestorRegistryEntry(
                investor_id="idle",
                display_name="Idle",
                dashboard_email=None,
                access_method="email",
                hostname="idle-portfolio.debopt.com",
                frontend_port=8799,
                live_enabled=False,
                frontend_enabled=False,
            ),
        ),
    )


def test_default_hostname_template():
    assert default_hostname("alice", "ignored.example.com", hostname_template="{id}-portfolio.debopt.com") == (
        "alice-portfolio.debopt.com"
    )
    assert default_hostname("alice", "portfolio.test") == "alice.portfolio.test"
    with pytest.raises(ConfigurationError):
        default_hostname("alice", None, hostname_template="no-placeholder.debopt.com")


def test_collect_ingress_rules_skips_disabled_and_sorts(tmp_path: Path):
    rules = collect_ingress_rules(_registry(tmp_path))
    assert [rule.investor_id for rule in rules] == ["jack", "eugene"]
    assert rules[0].service == "http://127.0.0.1:8766"
    assert rules[1].hostname == "yoeugene-portfolio.debopt.com"


def test_collect_ingress_rejects_example_com(tmp_path: Path):
    registry = PlatformRegistry(
        path=tmp_path / "registry.toml",
        platform=PlatformSettings(
            repo_root=tmp_path,
            python_bin="python3",
            domain="example.com",
            hostname_template=None,
            tunnel_name="debopt-jack",
            next_frontend_port=8800,
        ),
        investors=(
            InvestorRegistryEntry(
                investor_id="bob",
                display_name="Bob",
                dashboard_email=None,
                access_method="email",
                hostname="bob.portfolio.example.com",
                frontend_port=8765,
                live_enabled=True,
                frontend_enabled=True,
            ),
        ),
    )
    with pytest.raises(ConfigurationError, match="example.com"):
        collect_ingress_rules(registry)


def test_render_and_parse_local_config():
    text = render_local_config_yml(
        tunnel_id="abc",
        credentials_file="/tmp/abc.json",
        rules=collect_ingress_rules(_registry(Path("/tmp"))),
    )
    tunnel_id, creds = parse_local_tunnel_identity(text)
    assert tunnel_id == "abc"
    assert creds == "/tmp/abc.json"
    assert "yoeugene-portfolio.debopt.com" in text
    assert text.strip().endswith("service: http_status:404")


def test_merge_ingress_for_single_investor_preserves_others():
    existing = rules_to_remote_ingress(collect_ingress_rules(_registry(Path("/tmp"))))
    # drop eugene from "remote"
    existing = [row for row in existing if row.get("hostname") != "yoeugene-portfolio.debopt.com"]
    merged = merge_ingress_for_single_investor(
        existing,
        new_rule=collect_ingress_rules(_registry(Path("/tmp")), investor_id="eugene")[0],
    )
    hosts = [row.get("hostname") for row in merged if row.get("hostname")]
    assert "jack-portfolio.debopt.com" in hosts
    assert "yoeugene-portfolio.debopt.com" in hosts
    assert merged[-1]["service"] == "http_status:404"


def test_provision_tunnel_dry_run_local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from deribit_engine import tunnel_provision as tp

    cloud_dir = tmp_path / ".cloudflared"
    cloud_dir.mkdir()
    config_path = cloud_dir / "config.yml"
    config_path.write_text(
        "tunnel: b3fbbd02-9869-4829-8e51-ae89860d0a89\n"
        f"credentials-file: {cloud_dir / 'b3fbbd02-9869-4829-8e51-ae89860d0a89.json'}\n"
        "ingress:\n"
        "  - service: http_status:404\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tp, "cloudflared_config_path", lambda: config_path)
    monkeypatch.setattr(
        tp,
        "load_platform_registry",
        lambda repo_root=None, create_if_missing=False: _registry(tmp_path),
    )
    monkeypatch.setattr(tp, "resolve_effective_repo_root", lambda registry, cwd_repo=None: tmp_path)

    result = tp.provision_tunnel(
        repo_root=tmp_path,
        dry_run=True,
        sync_local=True,
        sync_remote=False,
        sync_dns=False,
    )
    assert result.ok
    assert result.dry_run
    assert result.local_changed
    assert result.tunnel_id == "b3fbbd02-9869-4829-8e51-ae89860d0a89"
    assert len(result.rules) == 2
