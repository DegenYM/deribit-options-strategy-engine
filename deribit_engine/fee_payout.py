"""Manager external payout addresses for investor fee payment (off-exchange)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .env_layout import find_repo_root
from .exceptions import ConfigurationError

FEE_PAYOUT_ADDRESSES_REL = Path("config/platform/fee-payout-addresses.toml")
FEE_PAYOUT_ADDRESSES_EXAMPLE_REL = Path("config/platform/fee-payout-addresses.toml.example")


@dataclass(frozen=True)
class FeePayoutAddress:
    asset: str
    network: str
    address: str
    notes: str | None = None


def fee_payout_addresses_path(repo_root: Path | str) -> Path:
    return Path(repo_root).resolve() / FEE_PAYOUT_ADDRESSES_REL


def load_fee_payout_addresses(
    repo_root: Path | str | None = None,
    *,
    required: bool = False,
) -> tuple[FeePayoutAddress, ...]:
    if repo_root is not None:
        root = Path(repo_root).resolve()
        if not (root / "deribit_engine").is_dir():
            root = find_repo_root(root) or root
    else:
        root = find_repo_root(Path.cwd())
    if root is None or not (root / "deribit_engine").is_dir():
        if required:
            raise ConfigurationError("Cannot locate repository root")
        return ()

    path = fee_payout_addresses_path(root)
    if not path.is_file():
        if required:
            example = root / FEE_PAYOUT_ADDRESSES_EXAMPLE_REL
            raise ConfigurationError(
                f"Missing {path}. Copy {example} to {path.name} and fill manager payout addresses."
            )
        return ()

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = data.get("addresses") or []
    if not isinstance(rows, list) or not rows:
        if required:
            raise ConfigurationError(f"No [[addresses]] rows in {path}")
        return ()

    out: list[FeePayoutAddress] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigurationError(f"addresses[{i}] must be a table")
        asset = str(row.get("asset") or "").strip().upper()
        network = str(row.get("network") or "").strip()
        address = str(row.get("address") or "").strip()
        if not asset or not network or not address:
            raise ConfigurationError(f"addresses[{i}] requires asset, network, and address")
        notes_raw = row.get("notes")
        notes = str(notes_raw).strip() if notes_raw is not None and str(notes_raw).strip() else None
        out.append(FeePayoutAddress(asset=asset, network=network, address=address, notes=notes))
    return tuple(out)


def format_fee_payout_addresses_markdown(addresses: tuple[FeePayoutAddress, ...]) -> str:
    if not addresses:
        return "_（管理方尚未設定 `config/platform/fee-payout-addresses.toml`）_"
    lines = ["| 幣別 | 鏈／網路 | 地址 | 備註 |", "|------|----------|------|------|"]
    for row in addresses:
        notes = row.notes or "—"
        lines.append(f"| {row.asset} | {row.network} | `{row.address}` | {notes} |")
    return "\n".join(lines)
