from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.config import load_config
from deribit_engine.env_layout import (
    env_layer_paths,
    investor_frontend_ledger_dir,
    investor_live_log_dir,
    investor_metrics_db_path,
    load_investor_manifest,
    resolve_account_env_path,
    resolve_investor_env_path,
    resolve_investor_scope,
)
from deribit_engine.exceptions import ConfigurationError


def _write_layout(repo: Path) -> Path:
    (repo / "deribit_engine").mkdir()
    (repo / "config" / "shared" / "strategies").mkdir(parents=True)
    (repo / "config" / "shared" / "strategies" / ".env.bull_put_spread").write_text(
        "OPTION_STRATEGY=bull_put_spread\n",
        encoding="utf-8",
    )
    tiers_dir = repo / "config" / "shared" / "strategies" / "tiers" / "bull_put_spread"
    tiers_dir.mkdir(parents=True)
    (tiers_dir / ".env.medium").write_text("SHORT_PUT_DELTA_MAX=0.17\n", encoding="utf-8")
    investor = repo / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    (investor / ".env.investor").write_text("DERIBIT_ENV=mainnet\nTARGET_PORTFOLIO_APR=0.30\n", encoding="utf-8")
    account = investor / "accounts" / ".env.bull_put"
    account.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "SHORT_PUT_DELTA_MAX=0.11",
                "REFERENCE_CAPITAL_USDC=2000",
                "STATE_FILE=.state/alpha/bull_put.json",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "bull_put"\nstrategy = "bull_put_spread"\n',
            ]
        ),
        encoding="utf-8",
    )
    return account


def test_env_layer_paths_merge_investor_account_strategy(tmp_path: Path):
    account = _write_layout(tmp_path)
    layers = env_layer_paths(account, "bull_put_spread")
    assert layers[-1].name == ".env.bull_put"
    config = load_config(account, require_private=False)
    assert config.env == "mainnet"
    assert config.short_put_delta_max == Decimal("0.11")
    assert config.reference_capital_usdc == Decimal("2000")
    assert str(config.target_portfolio_apr) == "0.30"


def test_load_investor_manifest_resolves_default_account_env(tmp_path: Path):
    _write_layout(tmp_path)
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert manifest.investor_id == "alpha"
    assert manifest.account_env_files()[0].name == ".env.bull_put"


def test_investor_scoped_runtime_paths(tmp_path: Path):
    account = _write_layout(tmp_path)
    assert resolve_investor_scope((account,), repo_root=tmp_path) == "alpha"
    assert investor_frontend_ledger_dir(tmp_path, "alpha") == tmp_path / "data/frontend_ledger/alpha"
    assert investor_metrics_db_path(tmp_path, "alpha") == tmp_path / "data/frontend_ledger/alpha/metrics.db"
    assert investor_live_log_dir(tmp_path, "alpha") == tmp_path / "logs/live/alpha"


def test_operational_accounts_skip_enabled_rows_without_api_creds(tmp_path: Path):
    _write_layout(tmp_path)
    investor = tmp_path / "config/investors/alpha"
    (investor / "accounts" / ".env.naked").write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=naked_short",
                "STATE_FILE=.state/alpha/naked.json",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "bull_put"\nstrategy = "bull_put_spread"\nenabled = true\n',
                '[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\nenabled = true\n',
            ]
        ),
        encoding="utf-8",
    )
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert [account.slug for account in manifest.enabled_accounts()] == ["bull_put", "naked"]
    assert [account.slug for account in manifest.accounts_without_creds()] == ["bull_put", "naked"]
    assert manifest.operational_accounts() == ()
    assert manifest.account_env_files(require_creds=True) == ()
    assert manifest.account_env_files()[0].name == ".env.bull_put"

    creds_account = investor / "accounts" / ".env.bull_put"
    creds_account.write_text(
        creds_account.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=test-id\nDERIBIT_CLIENT_SECRET=test-secret\n",
        encoding="utf-8",
    )
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert [account.slug for account in manifest.operational_accounts()] == ["bull_put"]
    assert [account.slug for account in manifest.accounts_without_creds()] == ["naked"]
    assert manifest.account_env_files(require_creds=True)[0].name == ".env.bull_put"


def test_live_operational_accounts_respect_live_enabled_flag(tmp_path: Path):
    _write_layout(tmp_path)
    investor = tmp_path / "config/investors/alpha"
    account = investor / "accounts" / ".env.bull_put"
    account.write_text(
        account.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=test-id\nDERIBIT_CLIENT_SECRET=test-secret\n",
        encoding="utf-8",
    )
    (investor / "accounts" / ".env.naked").write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=naked_short",
                "STATE_FILE=.state/alpha/naked.json",
                "DERIBIT_CLIENT_ID=test-id-2",
                "DERIBIT_CLIENT_SECRET=test-secret-2",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "bull_put"\nstrategy = "bull_put_spread"\nenabled = true\n',
                '[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\nenabled = true\nlive_enabled = false\n',
            ]
        ),
        encoding="utf-8",
    )
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert [account.slug for account in manifest.enabled_accounts()] == ["bull_put", "naked"]
    assert [account.slug for account in manifest.live_operational_accounts()] == ["bull_put"]
    assert [path.name for path in manifest.account_env_files(require_creds=True)] == [
        ".env.bull_put",
        ".env.naked",
    ]
    assert [path.name for path in manifest.account_env_files(require_creds=True, require_live=True)] == [
        ".env.bull_put",
    ]


def test_resolve_investor_scope_rejects_mixed_investors(tmp_path: Path):
    _write_layout(tmp_path)
    alpha_account = tmp_path / "config/investors/alpha/accounts/.env.bull_put"
    beta = tmp_path / "config/investors/beta"
    (beta / "accounts").mkdir(parents=True)
    (beta / "accounts.toml").write_text(
        '[investor]\nid = "beta"\n[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\n',
        encoding="utf-8",
    )
    beta_account = beta / "accounts/.env.naked"
    beta_account.write_text(
        "OPTION_STRATEGY=naked_short\nSTATE_FILE=.state/investors/beta/naked.json\n", encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match="multiple investors"):
        resolve_investor_scope((alpha_account, beta_account), repo_root=tmp_path)


def test_load_investor_manifest_normalizes_investor_id_casing(tmp_path: Path):
    _write_layout(tmp_path)
    manifest_path = tmp_path / "config/investors/alpha/accounts.toml"
    manifest_path.write_text(
        '[investor]\nid = "Alpha"\ndisplay_name = "Alpha"\n'
        '[[accounts]]\nslug = "bull_put"\nstrategy = "bull_put_spread"\n',
        encoding="utf-8",
    )
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert manifest.investor_id == "alpha"


def test_fee_env_layers_skip_strategy_profile(tmp_path: Path):
    repo = tmp_path
    (repo / "deribit_engine").mkdir()
    (repo / "config" / "shared" / "strategies").mkdir(parents=True)
    (repo / "config" / "shared" / "strategies" / ".env.naked_short").write_text(
        "MIN_NET_APR=0.99\n",
        encoding="utf-8",
    )
    investor = repo / "config" / "investors" / "pat"
    (investor / "accounts").mkdir(parents=True)
    fee_env = investor / "accounts" / ".env.fee"
    fee_env.write_text("ACCOUNT_ROLE=fee\nDERIBIT_ENV=mainnet\n", encoding="utf-8")

    layers = env_layer_paths(fee_env, "naked_short")

    assert fee_env in layers
    assert not any(".env.naked_short" in str(path) for path in layers)
    config = load_config(fee_env, require_private=False)
    assert config.min_net_apr == Decimal("0.12")


def test_legacy_account_env_path_emits_deprecation_warning(tmp_path: Path):
    investor = tmp_path / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    legacy = investor / "accounts" / "naked.env"
    legacy.write_text("OPTION_STRATEGY=naked_short\n", encoding="utf-8")
    with pytest.warns(DeprecationWarning, match="Legacy account env"):
        assert resolve_account_env_path(investor, "naked") == legacy.resolve()


def test_legacy_investor_env_path_emits_deprecation_warning(tmp_path: Path):
    investor = tmp_path / "config" / "investors" / "alpha"
    investor.mkdir(parents=True)
    legacy = investor / "investor.env"
    legacy.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    with pytest.warns(DeprecationWarning, match="Legacy investor env"):
        assert resolve_investor_env_path(investor) == legacy.resolve()


def test_legacy_defaults_env_emits_deprecation_warning(tmp_path: Path):
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "shared" / "defaults.env").write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    investor = tmp_path / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    account = investor / "accounts" / ".env.naked"
    account.write_text("OPTION_STRATEGY=naked_short\n", encoding="utf-8")
    with pytest.warns(DeprecationWarning, match="defaults.env"):
        layers = env_layer_paths(account, "naked_short")
    assert layers[0].name == "defaults.env"


def test_legacy_root_strategy_profile_emits_deprecation_warning(tmp_path: Path):
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / ".env.naked_short").write_text("MIN_NET_APR=0.99\n", encoding="utf-8")
    account = tmp_path / ".env"
    account.write_text("OPTION_STRATEGY=naked_short\n", encoding="utf-8")
    with pytest.warns(DeprecationWarning, match="Legacy strategy profile"):
        layers = env_layer_paths(account, "naked_short")
    assert tmp_path / ".env.naked_short" in layers


def test_env_layer_paths_applies_manifest_risk_tier(tmp_path: Path):
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "shared" / "strategies" / ".env.bull_put_spread").write_text(
        "OPTION_STRATEGY=bull_put_spread\n",
        encoding="utf-8",
    )
    tiers_dir = tmp_path / "config" / "shared" / "strategies" / "tiers" / "bull_put_spread"
    tiers_dir.mkdir(parents=True)
    (tiers_dir / ".env.medium").write_text("SHORT_PUT_DELTA_MAX=0.17\n", encoding="utf-8")
    (tiers_dir / ".env.low").write_text("SHORT_PUT_DELTA_MAX=0.12\n", encoding="utf-8")
    investor = tmp_path / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    account = investor / "accounts" / ".env.bull_put"
    account.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "STATE_FILE=.state/alpha/bull_put.json",
            ]
        ),
        encoding="utf-8",
    )
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "bull_put"\nstrategy = "bull_put_spread"\nrisk_tier = "low"\n',
            ]
        ),
        encoding="utf-8",
    )
    layers = env_layer_paths(account, "bull_put_spread")
    assert layers[-1].name == ".env.bull_put"
    assert tiers_dir / ".env.low" in layers
    config = load_config(account, require_private=False)
    assert config.risk_tier == "low"
    assert config.short_put_delta_max == Decimal("0.12")


def test_env_layer_paths_defaults_manifest_risk_tier_to_medium(tmp_path: Path):
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "shared" / "strategies" / ".env.naked_short").write_text(
        "OPTION_STRATEGY=naked_short\n",
        encoding="utf-8",
    )
    tiers_dir = tmp_path / "config" / "shared" / "strategies" / "tiers" / "naked_short"
    tiers_dir.mkdir(parents=True)
    (tiers_dir / ".env.medium").write_text("MIN_NET_APR=0.08\n", encoding="utf-8")
    (tiers_dir / ".env.high").write_text("MIN_NET_APR=0.12\n", encoding="utf-8")
    investor = tmp_path / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    account = investor / "accounts" / ".env.naked"
    account.write_text("DERIBIT_ENV=mainnet\nSTATE_FILE=.state/alpha/naked.json\n", encoding="utf-8")
    (investor / "accounts.toml").write_text(
        "\n".join(
            [
                '[investor]\nid = "alpha"\ndisplay_name = "Alpha"\n',
                '[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\n',
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(account, require_private=False)
    assert config.risk_tier == "medium"
    assert config.min_net_apr == Decimal("0.08")


def test_load_investor_manifest_reads_risk_tier(tmp_path: Path):
    account = _write_layout(tmp_path)
    manifest_path = tmp_path / "config/investors/alpha/accounts.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'strategy = "bull_put_spread"\n',
            'strategy = "bull_put_spread"\nrisk_tier = "high"\n',
        ),
        encoding="utf-8",
    )
    manifest = load_investor_manifest("alpha", repo_root=tmp_path)
    assert manifest.accounts[0].risk_tier == "high"
    assert account.is_file()
