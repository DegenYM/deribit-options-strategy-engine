from decimal import Decimal
from pathlib import Path

import pytest

from deribit_demo.config import load_config
from deribit_demo.env_layout import env_layer_paths, load_investor_manifest


def _write_layout(repo: Path) -> Path:
    (repo / "deribit_demo").mkdir()
    (repo / "config" / "shared" / "strategies").mkdir(parents=True)
    (repo / "config" / "shared" / "strategies" / ".env.bull_put_spread").write_text(
        "OPTION_STRATEGY=bull_put_spread\nSHORT_PUT_DELTA_MAX=0.17\n",
        encoding="utf-8",
    )
    investor = repo / "config" / "investors" / "alpha"
    (investor / "accounts").mkdir(parents=True)
    (investor / ".env.investor").write_text("DERIBIT_ENV=testnet\nTARGET_PORTFOLIO_APR=0.30\n", encoding="utf-8")
    account = investor / "accounts" / ".env.bull_put"
    account.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=bull_put_spread",
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
