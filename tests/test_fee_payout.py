from pathlib import Path

import pytest

from deribit_engine.exceptions import ConfigurationError
from deribit_engine.fee_payout import load_fee_payout_addresses


def test_load_fee_payout_addresses_missing_optional(tmp_path: Path) -> None:
    (tmp_path / "deribit_engine").mkdir()
    assert load_fee_payout_addresses(tmp_path) == ()


def test_load_fee_payout_addresses_parses(tmp_path: Path) -> None:
    (tmp_path / "deribit_engine").mkdir()
    path = tmp_path / "config/platform/fee-payout-addresses.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "[[addresses]]",
                'asset = "usdc"',
                'network = "Ethereum (ERC-20)"',
                'address = "0xabc"',
                'notes = "test"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    rows = load_fee_payout_addresses(tmp_path)
    assert len(rows) == 1
    assert rows[0].asset == "USDC"
    assert rows[0].address == "0xabc"


def test_load_fee_payout_addresses_required_raises(tmp_path: Path) -> None:
    (tmp_path / "deribit_engine").mkdir()
    with pytest.raises(ConfigurationError, match="fee-payout-addresses"):
        load_fee_payout_addresses(tmp_path, required=True)
