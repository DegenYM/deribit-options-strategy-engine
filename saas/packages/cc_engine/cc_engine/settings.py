from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ALLOWED_COINS = frozenset({"BTC", "ETH"})
ALLOWED_TIERS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class CoveredCallSettings:
    """Product-facing worker settings. Engine knobs stay in the platform catalog."""

    tenant_id: str
    risk_tier: str
    coins: tuple[str, ...]
    profit_sweep: bool
    live: bool
    state_dir: Path
    client_id: str
    client_secret: str
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def __post_init__(self) -> None:
        tier = self.risk_tier.strip().lower()
        if tier not in ALLOWED_TIERS:
            raise ValueError(f"risk_tier must be one of {sorted(ALLOWED_TIERS)}")
        coins = tuple(coin.upper() for coin in self.coins)
        if not coins or any(coin not in ALLOWED_COINS for coin in coins):
            raise ValueError("coins must be a non-empty subset of BTC, ETH")
        object.__setattr__(self, "risk_tier", tier)
        object.__setattr__(self, "coins", coins)
        object.__setattr__(self, "state_dir", Path(self.state_dir))

    @property
    def state_file(self) -> Path:
        return self.state_dir / "covered_call.json"

    @property
    def heartbeat_file(self) -> Path:
        return self.state_file.with_suffix(".heartbeat.json")

    @property
    def dry_run(self) -> bool:
        return not self.live
