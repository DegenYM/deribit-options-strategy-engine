from pathlib import Path

import pytest

from deribit_engine.exceptions import ConfigurationError
from deribit_engine.fee_snapshot_store import FeeSnapshotStore, fee_ledger_db_path
from deribit_engine.investor_ops import (
    bootstrap_initial_hwm,
    import_handoff,
    investor_init,
    is_hwm_bootstrapped,
    list_investors,
    parse_strategy_slugs,
    validate_investor,
)
from deribit_engine.investor_registry import load_platform_registry


def _bootstrap_repo(tmp_path: Path) -> Path:
    (tmp_path / "deribit_engine").mkdir()
    example = Path(__file__).resolve().parents[1] / "config" / "investors" / "_example"
    (tmp_path / "config" / "investors" / "_example").mkdir(parents=True)
    for rel in (
        "accounts.toml",
        ".env.investor.example",
        "accounts/.env.naked.example",
        "accounts/.env.bull_put.example",
        "accounts/.env.covered_call.example",
    ):
        src = example / rel
        dest = tmp_path / "config" / "investors" / "_example" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    for name in ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "launchd" / name
        (tmp_path / "config" / "launchd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "systemd").mkdir(parents=True)
    for name in ("com.deribit.live.service.template", "com.deribit.frontend.service.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "systemd" / name
        (tmp_path / "config" / "systemd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "python3"',
                'domain = "portfolio.test"',
                "next_frontend_port = 8800",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_parse_strategy_slugs_rejects_unknown():
    with pytest.raises(ConfigurationError):
        parse_strategy_slugs("naked,unknown")


def test_investor_init_scaffolds_manifest_and_registry(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    result = investor_init(
        "alice",
        strategies=("naked", "bull_put"),
        display_name="Alice",
        dashboard_email="alice@example.com",
        repo_root=repo,
    )
    assert result.investor_id == "alice"
    assert (repo / "config/investors/alice/accounts.toml").is_file()
    assert (repo / "config/investors/alice/accounts/.env.naked").is_file()
    assert (repo / "config/investors/alice/accounts/.env.fee").is_file()
    fee_text = (repo / "config/investors/alice/accounts/.env.fee").read_text(encoding="utf-8")
    assert "ACCOUNT_ROLE=fee" in fee_text
    assert result.frontend_port == 8800

    registry = load_platform_registry(repo_root=repo)
    entry = registry.entry_for("alice")
    assert entry is not None
    assert entry.dashboard_email == "alice@example.com"
    assert entry.hostname == "alice.portfolio.test"


def test_import_handoff_writes_credentials(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    investor_init("bob", strategies=("naked",), repo_root=repo)

    handoff = tmp_path / "handoff.toml"
    handoff.write_text(
        "\n".join(
            [
                "[investor]",
                'id = "bob"',
                'deribit_env = "mainnet"',
                "",
                "[[strategies]]",
                'slug = "naked"',
                'client_id = "cid"',
                'client_secret = "sec"',
                "reference_capital_usdc = 42000",
                "",
                "[fee]",
                'client_id = "feeid"',
                'client_secret = "feesec"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    outcome = import_handoff(handoff, repo_root=repo)
    assert outcome["strategies_updated"] == ["naked"]
    assert outcome["fee_updated"] is True

    env_text = (repo / "config/investors/bob/accounts/.env.naked").read_text(encoding="utf-8")
    assert "DERIBIT_CLIENT_ID=cid" in env_text
    assert "REFERENCE_CAPITAL_USDC=42000" in env_text

    result = validate_investor("bob", check_api=False, repo_root=repo)
    assert result.ok is True


def _import_minimal_handoff(repo: Path, tmp_path: Path, investor_id: str) -> None:
    handoff = tmp_path / f"{investor_id}-handoff.toml"
    handoff.write_text(
        "\n".join(
            [
                "[investor]",
                f'id = "{investor_id}"',
                'deribit_env = "mainnet"',
                "",
                "[[strategies]]",
                'slug = "naked"',
                'client_id = "cid"',
                'client_secret = "sec"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    import_handoff(handoff, repo_root=repo)


def test_validate_skips_hwm_bootstrap_with_no_api(tmp_path: Path, monkeypatch):
    repo = _bootstrap_repo(tmp_path)
    investor_init("dana", strategies=("naked",), repo_root=repo)
    _import_minimal_handoff(repo, tmp_path, "dana")
    called = {"n": 0}

    def _fail_if_called(*_a, **_k):
        called["n"] += 1
        raise AssertionError("bootstrap should not run")

    monkeypatch.setattr(
        "deribit_engine.investor_ops.bootstrap_initial_hwm",
        _fail_if_called,
    )
    result = validate_investor("dana", check_api=False, repo_root=repo)
    assert result.ok is True
    assert result.hwm_bootstrap is None
    assert called["n"] == 0


def test_validate_bootstraps_hwm_when_api_ok(tmp_path: Path, monkeypatch):
    from deribit_engine.client import DeribitClient

    repo = _bootstrap_repo(tmp_path)
    investor_init("erin", strategies=("naked",), repo_root=repo)
    handoff = tmp_path / "handoff.toml"
    handoff.write_text(
        "\n".join(
            [
                "[investor]",
                'id = "erin"',
                'deribit_env = "mainnet"',
                "",
                "[[strategies]]",
                'slug = "naked"',
                'client_id = "cid"',
                'client_secret = "sec"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    import_handoff(handoff, repo_root=repo)

    calls: list[str] = []

    def _fake_bootstrap(investor_id: str, **kwargs):
        calls.append(investor_id)
        return {
            "skipped": False,
            "snapshot_id": 1,
            "nav_perf": "1000",
            "hwm_bootstrap": {"source": "transaction_log", "initial_hwm_nav_perf": "800"},
            "initial_hwm_nav_perf": "800",
        }

    monkeypatch.setattr(
        "deribit_engine.investor_ops.bootstrap_initial_hwm",
        _fake_bootstrap,
    )
    monkeypatch.setattr(
        DeribitClient,
        "get_account_summaries",
        lambda self, extended=False: [],
    )

    result = validate_investor("erin", check_api=True, repo_root=repo)
    assert result.ok is True
    assert calls == ["erin"]
    assert result.hwm_bootstrap is not None
    assert result.hwm_bootstrap["initial_hwm_nav_perf"] == "800"


def test_validate_no_bootstrap_hwm_flag(tmp_path: Path, monkeypatch):
    repo = _bootstrap_repo(tmp_path)
    investor_init("gina", strategies=("naked",), repo_root=repo)
    _import_minimal_handoff(repo, tmp_path, "gina")
    called = {"n": 0}

    def _fail(*_a, **_k):
        called["n"] += 1

    monkeypatch.setattr("deribit_engine.investor_ops.bootstrap_initial_hwm", _fail)
    result = validate_investor(
        "gina",
        check_api=False,
        bootstrap_hwm=False,
        repo_root=repo,
    )
    assert result.ok is True
    assert called["n"] == 0


def test_bootstrap_initial_hwm_skips_when_already_set(tmp_path: Path):
    from decimal import Decimal

    repo = _bootstrap_repo(tmp_path)
    investor_init("frank", strategies=("naked",), repo_root=repo)
    store = FeeSnapshotStore(fee_ledger_db_path(repo, "frank"))
    store.save_hwm(investor_id="frank", hwm_nav_perf=Decimal("12345"), updated_at_ms=1)
    assert is_hwm_bootstrapped(store, "frank") is True
    outcome = bootstrap_initial_hwm("frank", repo_root=repo)
    assert outcome["skipped"] is True
    assert outcome["initial_hwm_nav_perf"] == "12345"


def test_list_investors_merges_registry_and_disk(tmp_path: Path):
    repo = _bootstrap_repo(tmp_path)
    investor_init("carol", strategies=("naked",), register=False, repo_root=repo)
    rows = list_investors(repo_root=repo)
    ids = {row["investor_id"] for row in rows}
    assert "carol" in ids
