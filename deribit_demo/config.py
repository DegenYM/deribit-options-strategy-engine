from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

from .env_layout import env_layer_paths
from .exceptions import ConfigurationError
from .utils import parse_csv, to_decimal


@dataclass(frozen=True)
class BotConfig:
    env: str
    client_id: str
    client_secret: str
    option_strategy: str
    option_markets_profile: str
    managed_currencies: tuple[str, ...]
    top_n: int
    reference_capital_usdc: Decimal
    target_portfolio_apr: Decimal
    entry_dte_min: int
    entry_dte_max: int
    short_put_delta_min: Decimal
    short_put_delta_max: Decimal
    preferred_short_put_delta_min: Decimal
    preferred_short_put_delta_max: Decimal
    put_otm_min: Decimal
    put_otm_max: Decimal
    min_liquid_expiries_required: int
    halt_open_max_loss_pct: Decimal
    tp_capture_pct: Decimal
    enable_early_exit: bool
    early_exit_remaining_apr: Decimal
    early_exit_min_profit_capture: Decimal
    early_exit_max_spread_ratio: Decimal
    time_exit_dte: int
    soft_defense_delta: Decimal
    hard_defense_delta: Decimal
    soft_defense_loss_pct: Decimal
    hard_stop_loss_pct: Decimal
    cooldown_hours: int
    poll_seconds_normal: int
    poll_seconds_stress: int
    short_entry_wait_seconds: int
    order_poll_seconds: int
    option_fee_rate: Decimal
    option_fee_cap_rate: Decimal
    exit_buffer_ratio: Decimal
    index_drawdown_elevated_pct: Decimal
    index_drawdown_crisis_pct: Decimal
    dvol_elevated_multiplier: Decimal
    dvol_crisis_multiplier: Decimal
    halt_drawdown_pct: Decimal
    hard_derisk_drawdown_pct: Decimal
    hard_derisk_maintenance_margin_ratio: Decimal
    hard_derisk_on_crisis_open_group: bool
    enable_perp_hedge: bool
    soft_hedge_delta_cap_pct: Decimal
    hard_hedge_delta_cap_pct: Decimal
    max_concurrent_groups: int
    max_groups_per_currency: int
    recovery_normal_cycles: int
    order_label_prefix: str
    request_timeout_seconds: int
    state_file: Path
    min_net_apr: Decimal
    target_net_apr_min: Decimal
    target_net_apr_max: Decimal
    btc_put_delta_min: Decimal
    btc_put_delta_max: Decimal
    eth_put_delta_min: Decimal
    eth_put_delta_max: Decimal
    btc_put_otm_min: Decimal
    btc_put_otm_max: Decimal
    eth_put_otm_min: Decimal
    eth_put_otm_max: Decimal
    btc_preferred_put_delta_min: Decimal
    btc_preferred_put_delta_max: Decimal
    eth_preferred_put_delta_min: Decimal
    eth_preferred_put_delta_max: Decimal
    btc_preferred_otm_min: Decimal
    btc_preferred_otm_max: Decimal
    eth_preferred_otm_min: Decimal
    eth_preferred_otm_max: Decimal
    enable_naked_topup: bool
    enable_adopt_exchange_positions: bool
    # Stage B: short call mirror of short put gates.
    enable_short_put: bool
    enable_short_call: bool
    short_call_delta_min: Decimal
    short_call_delta_max: Decimal
    preferred_short_call_delta_min: Decimal
    preferred_short_call_delta_max: Decimal
    call_otm_min: Decimal
    call_otm_max: Decimal
    btc_call_delta_min: Decimal
    btc_call_delta_max: Decimal
    eth_call_delta_min: Decimal
    eth_call_delta_max: Decimal
    btc_call_otm_min: Decimal
    btc_call_otm_max: Decimal
    eth_call_otm_min: Decimal
    eth_call_otm_max: Decimal
    btc_preferred_call_delta_min: Decimal
    btc_preferred_call_delta_max: Decimal
    eth_preferred_call_delta_min: Decimal
    eth_preferred_call_delta_max: Decimal
    btc_preferred_call_otm_min: Decimal
    btc_preferred_call_otm_max: Decimal
    eth_preferred_call_otm_min: Decimal
    eth_preferred_call_otm_max: Decimal
    bull_put_long_delta_min: Decimal = Decimal("0.02")
    bull_put_long_delta_max: Decimal = Decimal("0.05")
    # --- Three-book strategy fields (defaults apply when not overridden) ---
    # Per-leg IM caps split by option side so call legs (unbounded upside
    # exposure) can be tightened versus put legs under the same book.
    per_leg_im_cap_put: Decimal = Decimal("0.15")
    per_leg_im_cap_call: Decimal = Decimal("0.12")
    # Per-book shared caps (shared between BTC/ETH/USDC books).
    expiry_im_cap_per_book: Decimal = Decimal("0.30")
    book_im_target: Decimal = Decimal("0.35")
    book_im_hard: Decimal = Decimal("0.45")
    book_mm_target: Decimal = Decimal("0.22")
    book_mm_hard: Decimal = Decimal("0.33")
    # Short call falls back only when no put candidate is available.
    short_call_fallback_only: bool = True
    # Minimum cooldown between two entries in the same book.
    entry_cooldown_minutes: int = 20
    # How often to cancel+requote unfilled entry orders.
    reprice_minutes: int = 3
    # Liquidity gates split by collateral type.
    inverse_min_open_interest: Decimal = Decimal("20")
    btc_inverse_min_open_interest: Decimal | None = None
    eth_inverse_min_open_interest: Decimal | None = None
    inverse_max_spread_ratio: Decimal = Decimal("0.12")
    inverse_min_book_notional_usdc: Decimal = Decimal("3000")
    linear_min_open_interest: Decimal = Decimal("8")
    btc_linear_min_open_interest: Decimal | None = None
    eth_linear_min_open_interest: Decimal | None = None
    linear_max_spread_ratio: Decimal = Decimal("0.14")
    linear_min_book_notional_usdc: Decimal = Decimal("4000")
    # Separate per-book group cap (superseding max_groups_per_currency once books roll out).
    max_groups_per_book: int = 3
    # Call-side defence thresholds (short put uses hard_defense_delta / hard_stop_loss_pct).
    soft_defense_delta_call: Decimal = Decimal("0.18")
    hard_defense_delta_call: Decimal = Decimal("0.24")
    # Raw SCAN_ASSETS override kept alongside managed_currencies for future use.
    scan_assets: tuple[str, ...] = ()
    # --- Collateral routing (Stage C: decouple scan vs book management) -----
    # ``scan_underlyings`` lists the underlyings the scanner looks at (BTC, ETH).
    # ``traded_collaterals`` lists which collateral pools the engine treats as
    # live risk books. A pool not in this list is *not* built by BookRouter,
    # so its equity (including dust) is ignored for drawdown / IM gates.
    scan_underlyings: tuple[str, ...] = ("BTC", "ETH")
    traded_collaterals: tuple[str, ...] = ("BTC", "ETH", "USDC")
    # Minimum USDC-equivalent equity for a book to count as "live". Books under
    # this floor are excluded from drawdown calculation even if present in
    # ``traded_collaterals``; this protects against phantom drawdowns driven by
    # tiny dust balances left in BTC / ETH sub-accounts.
    min_book_equity_usdc: Decimal = Decimal("0")
    # How often (seconds) to refresh the external cash-flow sum via Deribit's
    # private/get_transaction_log. Lower = more responsive to manual
    # withdrawals; higher = fewer API calls.
    cash_flow_query_interval_seconds: int = 300
    # Covered-call spot exit controls. Disabled by default so covered_call keeps
    # the existing option-only behavior unless the profile explicitly opts in.
    covered_call_spot_exit_enabled: bool = False
    covered_call_robust_exit_enabled: bool = False
    covered_call_robust_exit_dte: Decimal = Decimal("0.5")
    covered_call_itm_buffer_pct: Decimal = Decimal("0")
    covered_call_spot_order_type: str = "market"

    @property
    def rest_base_url(self) -> str:
        if self.env == "testnet":
            return "https://test.deribit.com/api/v2"
        return "https://www.deribit.com/api/v2"

    @property
    def has_private_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def uses_naked_short_options(self) -> bool:
        return self.option_strategy == "naked_short"

    @property
    def naked_scan_put_and_call_compete(self) -> bool:
        """True when scan ranks puts and calls together (not put-then-fallback)."""
        return self.enable_short_put and self.enable_short_call and not self.short_call_fallback_only

    @property
    def target_annual_net_pnl_usdc(self) -> Decimal:
        return self.reference_capital_usdc * self.target_portfolio_apr

    def put_delta_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_put_delta_min, self.btc_put_delta_max
        if c == "ETH":
            return self.eth_put_delta_min, self.eth_put_delta_max
        return self.short_put_delta_min, self.short_put_delta_max

    def put_otm_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_put_otm_min, self.btc_put_otm_max
        if c == "ETH":
            return self.eth_put_otm_min, self.eth_put_otm_max
        return self.put_otm_min, self.put_otm_max

    def preferred_put_delta_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_preferred_put_delta_min, self.btc_preferred_put_delta_max
        if c == "ETH":
            return self.eth_preferred_put_delta_min, self.eth_preferred_put_delta_max
        return self.preferred_short_put_delta_min, self.preferred_short_put_delta_max

    def preferred_put_otm_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_preferred_otm_min, self.btc_preferred_otm_max
        if c == "ETH":
            return self.eth_preferred_otm_min, self.eth_preferred_otm_max
        return Decimal("0.09"), Decimal("0.12")

    def per_leg_im_cap(self, currency: str, option_type: str = "put") -> Decimal:
        """Per-leg IM cap for a given underlying and option side.

        The three-book model uses the same side-aware cap across books, so the
        ``currency`` argument is kept only for backward compatibility with
        callers that still route per currency. ``option_type`` selects the
        tighter call cap or the looser put cap.
        """
        return self.per_leg_im_cap_call if option_type == "call" else self.per_leg_im_cap_put

    def expiry_im_cap(self, currency: str) -> Decimal:
        """Per-expiry IM cap; same value across books under the new model."""
        _ = currency
        return self.expiry_im_cap_per_book

    def hard_mm_utilization(self, currency: str) -> Decimal:
        """Hard MM utilisation for the book that owns ``currency``.

        All three books share ``book_mm_hard`` so the cap is the same
        regardless of currency. Kept per-currency for legacy callers that
        still look up BTC/ETH directly.
        """
        _ = currency
        return self.book_mm_hard

    def call_delta_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_call_delta_min, self.btc_call_delta_max
        if c == "ETH":
            return self.eth_call_delta_min, self.eth_call_delta_max
        return self.short_call_delta_min, self.short_call_delta_max

    def call_otm_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_call_otm_min, self.btc_call_otm_max
        if c == "ETH":
            return self.eth_call_otm_min, self.eth_call_otm_max
        return self.call_otm_min, self.call_otm_max

    def preferred_call_delta_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_preferred_call_delta_min, self.btc_preferred_call_delta_max
        if c == "ETH":
            return self.eth_preferred_call_delta_min, self.eth_preferred_call_delta_max
        return self.preferred_short_call_delta_min, self.preferred_short_call_delta_max

    def preferred_call_otm_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        c = currency.upper()
        if c == "BTC":
            return self.btc_preferred_call_otm_min, self.btc_preferred_call_otm_max
        if c == "ETH":
            return self.eth_preferred_call_otm_min, self.eth_preferred_call_otm_max
        return Decimal("0.09"), Decimal("0.12")

    def delta_bounds(self, currency: str, option_type: str) -> tuple[Decimal, Decimal]:
        return self.call_delta_bounds(currency) if option_type == "call" else self.put_delta_bounds(currency)

    def otm_bounds(self, currency: str, option_type: str) -> tuple[Decimal, Decimal]:
        return self.call_otm_bounds(currency) if option_type == "call" else self.put_otm_bounds(currency)

    def preferred_delta_bounds(self, currency: str, option_type: str) -> tuple[Decimal, Decimal]:
        return (
            self.preferred_call_delta_bounds(currency)
            if option_type == "call"
            else self.preferred_put_delta_bounds(currency)
        )

    def preferred_otm_bounds(self, currency: str, option_type: str) -> tuple[Decimal, Decimal]:
        return (
            self.preferred_call_otm_bounds(currency)
            if option_type == "call"
            else self.preferred_put_otm_bounds(currency)
        )

    def min_open_interest(self, instrument_type: str, currency: str = "") -> Decimal:
        """Return the open-interest floor for an instrument type and underlying."""
        c = currency.upper()
        if (instrument_type or "").lower() == "linear":
            if c == "BTC" and self.btc_linear_min_open_interest is not None:
                return self.btc_linear_min_open_interest
            if c == "ETH" and self.eth_linear_min_open_interest is not None:
                return self.eth_linear_min_open_interest
            return self.linear_min_open_interest
        if c == "BTC" and self.btc_inverse_min_open_interest is not None:
            return self.btc_inverse_min_open_interest
        if c == "ETH" and self.eth_inverse_min_open_interest is not None:
            return self.eth_inverse_min_open_interest
        return self.inverse_min_open_interest

    def liquidity_gates(self, instrument_type: str, currency: str = "") -> tuple[Decimal, Decimal, Decimal]:
        """Return ``(min_oi, max_spread_ratio, min_book_notional_usdc)`` for a given
        instrument. ``instrument_type == "linear"`` selects the linear-USDC gates;
        anything else (including the empty string) falls back to the inverse gates.
        When ``currency`` is provided, the open-interest floor can be overridden
        per underlying while spread and notional gates remain per instrument type.
        """
        if (instrument_type or "").lower() == "linear":
            return (
                self.min_open_interest(instrument_type, currency),
                self.linear_max_spread_ratio,
                self.linear_min_book_notional_usdc,
            )
        return (
            self.min_open_interest(instrument_type, currency),
            self.inverse_max_spread_ratio,
            self.inverse_min_book_notional_usdc,
        )


def _optional(values: dict[str, str], key: str, default: str = "") -> str:
    return values.get(key, default)


def _optional_decimal(values: dict[str, str], key: str, default: str = "") -> Decimal | None:
    raw = _optional(values, key, default)
    return to_decimal(raw) if raw != "" else None


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if not value:
        raise ConfigurationError(f"Missing required config: {key}")
    return value


def _to_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _to_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean config value: {value}")


def _short_option_side_overrides(raw: str) -> tuple[bool, bool, bool] | None:
    """Parse SHORT_OPTION_SIDE into put/call/fallback settings.

    ``both`` means puts and calls compete in the same scan. Leaving this unset
    preserves the legacy ENABLE_SHORT_PUT / ENABLE_SHORT_CALL /
    SHORT_CALL_FALLBACK_ONLY behaviour.
    """
    if not raw:
        return None
    normalized = raw.strip().lower().replace("-", "_").replace(" ", "")
    aliases = {
        "p": "put",
        "puts": "put",
        "short_put": "put",
        "c": "call",
        "calls": "call",
        "short_call": "call",
        "all": "both",
        "both": "both",
        "put_call": "both",
        "call_put": "both",
        "put,call": "both",
        "call,put": "both",
    }
    side = aliases.get(normalized, normalized)
    if side == "put":
        return True, False, True
    if side == "call":
        return False, True, True
    if side == "both":
        return True, True, False
    raise ConfigurationError("SHORT_OPTION_SIDE must be one of: put, call, both")


def _option_strategy(raw: str) -> str:
    normalized = (raw or "naked_short").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "naked": "naked_short",
        "naked_put": "naked_short",
        "naked_call": "naked_short",
        "short_put": "naked_short",
        "short_call": "naked_short",
        "shortput": "naked_short",
        "shortcall": "naked_short",
        "naked_short_put": "naked_short",
        "naked_short_call": "naked_short",
        "put_spread": "bull_put_spread",
        "bullputspread": "bull_put_spread",
        "bull_put": "bull_put_spread",
        "coveredcall": "covered_call",
    }
    strategy = aliases.get(normalized, normalized)
    allowed = {"naked_short", "bull_put_spread", "covered_call"}
    if strategy not in allowed:
        raise ConfigurationError("OPTION_STRATEGY must be one of: naked_short, bull_put_spread, covered_call")
    return strategy


def _env_values(env_file: str | Path) -> dict[str, str]:
    return {k: v for k, v in dotenv_values(env_file).items() if v is not None}


def _load_env_values_with_strategy_profile(
    env_file: str | Path,
    *,
    strategy_override: str | None = None,
) -> dict[str, str]:
    env_path = Path(env_file)
    if not env_path.exists():
        raise ConfigurationError(f"Env file not found: {env_path}")

    seed: dict[str, str] = {}
    if env_path.is_file():
        seed = _env_values(env_path)
    base_strategy = _option_strategy(
        strategy_override or _optional(seed, "OPTION_STRATEGY", "naked_short")
    )

    values: dict[str, str] = {}
    for layer_path in env_layer_paths(env_path, base_strategy):
        layer_values = _env_values(layer_path)
        if layer_path == env_path:
            values.update(layer_values)
            continue
        profile_strategy_raw = _optional(layer_values, "OPTION_STRATEGY")
        if profile_strategy_raw:
            profile_strategy = _option_strategy(profile_strategy_raw)
            if profile_strategy != base_strategy:
                raise ConfigurationError(
                    f"{layer_path} OPTION_STRATEGY={profile_strategy} "
                    f"does not match account OPTION_STRATEGY={base_strategy}"
                )
        values.update(layer_values)

    values["OPTION_STRATEGY"] = base_strategy
    return values


def load_config(
    env_file: str | Path = ".env",
    require_private: bool = False,
    *,
    strategy_override: str | None = None,
) -> BotConfig:
    values = _load_env_values_with_strategy_profile(
        env_file,
        strategy_override=strategy_override,
    )
    env = _optional(values, "DERIBIT_ENV", "testnet").lower()
    if env not in {"mainnet", "testnet", "prod"}:
        raise ConfigurationError("DERIBIT_ENV must be mainnet, testnet, or prod")
    if env == "prod":
        env = "mainnet"

    client_id = _optional(values, "DERIBIT_CLIENT_ID")
    client_secret = _optional(values, "DERIBIT_CLIENT_SECRET")
    if require_private:
        _required(values, "DERIBIT_CLIENT_ID")
        _required(values, "DERIBIT_CLIENT_SECRET")

    option_strategy = _option_strategy(_optional(values, "OPTION_STRATEGY", "naked_short"))
    option_markets_profile = _optional(values, "OPTION_MARKETS_PROFILE", "all").lower()
    if option_markets_profile not in {"inverse_native", "all", "linear_usdc"}:
        raise ConfigurationError(
            "OPTION_MARKETS_PROFILE must be one of: all, linear_usdc, inverse_native"
        )

    book_im_hard_value = to_decimal(_optional(values, "BOOK_IM_HARD", "0.45"))

    scan_assets = parse_csv(_optional(values, "SCAN_ASSETS", ""), upper=True)
    managed_currencies = scan_assets or parse_csv(_optional(values, "MANAGED_CURRENCIES", "BTC,ETH"), upper=True) or ("BTC", "ETH")

    # Stage C: ``SCAN_UNDERLYINGS`` aliases the legacy ``MANAGED_CURRENCIES`` /
    # ``SCAN_ASSETS`` list and defines which underlyings the scanner looks at.
    # ``TRADED_COLLATERALS`` is a new, independent lever that selects which
    # collateral pools the engine builds books for (BTC, ETH, USDC). When
    # absent, every collateral pool is built (backward compatible).
    scan_underlyings = (
        parse_csv(_optional(values, "SCAN_UNDERLYINGS", ""), upper=True)
        or managed_currencies
    )
    traded_collaterals = (
        parse_csv(_optional(values, "TRADED_COLLATERALS", ""), upper=True)
        or ("BTC", "ETH", "USDC")
    )
    min_book_equity_usdc = to_decimal(_optional(values, "MIN_BOOK_EQUITY_USDC", "0"))
    cash_flow_query_interval_seconds = max(
        30,
        int(_optional(values, "CASH_FLOW_QUERY_INTERVAL_SECONDS", "300")),
    )
    covered_call_spot_order_type = _optional(values, "COVERED_CALL_SPOT_ORDER_TYPE", "market").lower()
    if covered_call_spot_order_type != "market":
        raise ConfigurationError("COVERED_CALL_SPOT_ORDER_TYPE currently supports only: market")

    put_dte_min = int(_optional(values, "PUT_DTE_MIN", _optional(values, "ENTRY_DTE_MIN", "10")))
    put_dte_max = int(_optional(values, "PUT_DTE_MAX", _optional(values, "ENTRY_DTE_MAX", "21")))
    side_override = _short_option_side_overrides(_optional(values, "SHORT_OPTION_SIDE", ""))
    if side_override is None:
        enable_short_put = _to_bool(_optional(values, "ENABLE_SHORT_PUT", "true"), default=True)
        enable_short_call = _to_bool(_optional(values, "ENABLE_SHORT_CALL", "false"))
        short_call_fallback_only = _to_bool(
            _optional(values, "SHORT_CALL_FALLBACK_ONLY", "true"), default=True
        )
    else:
        enable_short_put, enable_short_call, short_call_fallback_only = side_override

    inverse_min_open_interest = to_decimal(
        _optional(values, "INVERSE_MIN_OPEN_INTEREST", _optional(values, "MIN_OPEN_INTEREST", "20"))
    )
    linear_min_open_interest = to_decimal(_optional(values, "LINEAR_MIN_OPEN_INTEREST", "8"))
    btc_min_open_interest = _optional(values, "BTC_MIN_OPEN_INTEREST")
    eth_min_open_interest = _optional(values, "ETH_MIN_OPEN_INTEREST")
    btc_inverse_min_open_interest = _optional_decimal(values, "BTC_INVERSE_MIN_OPEN_INTEREST", btc_min_open_interest)
    eth_inverse_min_open_interest = _optional_decimal(values, "ETH_INVERSE_MIN_OPEN_INTEREST", eth_min_open_interest)
    btc_linear_min_open_interest = _optional_decimal(values, "BTC_LINEAR_MIN_OPEN_INTEREST", btc_min_open_interest)
    eth_linear_min_open_interest = _optional_decimal(values, "ETH_LINEAR_MIN_OPEN_INTEREST", eth_min_open_interest)

    return BotConfig(
        env=env,
        client_id=client_id,
        client_secret=client_secret,
        option_strategy=option_strategy,
        option_markets_profile=option_markets_profile,
        managed_currencies=managed_currencies,
        top_n=int(_optional(values, "TOP_N", "5")),
        reference_capital_usdc=to_decimal(_optional(values, "REFERENCE_CAPITAL_USDC", "1000")),
        target_portfolio_apr=to_decimal(_optional(values, "TARGET_PORTFOLIO_APR", "0.20")),
        entry_dte_min=put_dte_min,
        entry_dte_max=put_dte_max,
        short_put_delta_min=to_decimal(_optional(values, "SHORT_PUT_DELTA_MIN", "0.08")),
        short_put_delta_max=to_decimal(_optional(values, "SHORT_PUT_DELTA_MAX", "0.14")),
        preferred_short_put_delta_min=to_decimal(_optional(values, "PREFERRED_SHORT_PUT_DELTA_MIN", "0.10")),
        preferred_short_put_delta_max=to_decimal(_optional(values, "PREFERRED_SHORT_PUT_DELTA_MAX", "0.12")),
        put_otm_min=to_decimal(_optional(values, "PUT_OTM_MIN", "0.08")),
        put_otm_max=to_decimal(_optional(values, "PUT_OTM_MAX", "0.18")),
        min_liquid_expiries_required=max(1, int(_optional(values, "MIN_LIQUID_EXPIRIES_REQUIRED", "2"))),
        halt_open_max_loss_pct=to_decimal(
            _optional(values, "OPEN_MAX_LOSS_HALT_RATIO", str(book_im_hard_value))
        ),
        tp_capture_pct=to_decimal(_optional(values, "TP_CAPTURE_PCT", "0.60")),
        enable_early_exit=_to_bool(_optional(values, "ENABLE_EARLY_EXIT", "true"), default=True),
        early_exit_remaining_apr=to_decimal(_optional(values, "EARLY_EXIT_REMAINING_APR", "0.08")),
        early_exit_min_profit_capture=to_decimal(_optional(values, "EARLY_EXIT_MIN_PROFIT_CAPTURE", "0.25")),
        early_exit_max_spread_ratio=to_decimal(_optional(values, "EARLY_EXIT_MAX_SPREAD_RATIO", "0.05")),
        time_exit_dte=int(_optional(values, "TIME_EXIT_DTE", "5")),
        soft_defense_delta=to_decimal(_optional(values, "SOFT_DEFENSE_DELTA", "0.25")),
        hard_defense_delta=to_decimal(_optional(values, "HARD_DEFENSE_DELTA", "0.35")),
        soft_defense_loss_pct=to_decimal(_optional(values, "SOFT_DEFENSE_LOSS_PCT", "0.35")),
        hard_stop_loss_pct=to_decimal(_optional(values, "HARD_STOP_LOSS_PCT", "0.55")),
        cooldown_hours=int(_optional(values, "COOLDOWN_HOURS", "24")),
        poll_seconds_normal=int(_optional(values, "POLL_SECONDS_NORMAL", "15")),
        poll_seconds_stress=int(_optional(values, "POLL_SECONDS_STRESS", "5")),
        short_entry_wait_seconds=int(_optional(values, "SHORT_ENTRY_WAIT_SECONDS", "120")),
        order_poll_seconds=int(_optional(values, "ORDER_POLL_SECONDS", "10")),
        option_fee_rate=to_decimal(_optional(values, "OPTION_FEE_RATE", "0.0003")),
        option_fee_cap_rate=to_decimal(_optional(values, "OPTION_FEE_CAP_RATE", "0.125")),
        exit_buffer_ratio=to_decimal(_optional(values, "EXIT_BUFFER_RATIO", "0.03")),
        index_drawdown_elevated_pct=to_decimal(_optional(values, "INDEX_DRAWDOWN_ELEVATED_PCT", "0.04")),
        index_drawdown_crisis_pct=to_decimal(_optional(values, "INDEX_DRAWDOWN_CRISIS_PCT", "0.06")),
        dvol_elevated_multiplier=to_decimal(_optional(values, "DVOL_ELEVATED_MULTIPLIER", "1.25")),
        dvol_crisis_multiplier=to_decimal(_optional(values, "DVOL_CRISIS_MULTIPLIER", "1.60")),
        halt_drawdown_pct=to_decimal(_optional(values, "HALT_DRAWDOWN_PCT", "0.03")),
        hard_derisk_drawdown_pct=to_decimal(_optional(values, "HARD_DERISK_DRAWDOWN_PCT", "0.06")),
        hard_derisk_maintenance_margin_ratio=to_decimal(_optional(values, "HARD_DERISK_MAINTENANCE_MARGIN_RATIO", "0.12")),
        hard_derisk_on_crisis_open_group=_to_bool(_optional(values, "HARD_DERISK_ON_CRISIS_OPEN_GROUP", "false")),
        enable_perp_hedge=_to_bool(_optional(values, "ENABLE_PERP_HEDGE", "false")),
        soft_hedge_delta_cap_pct=to_decimal(_optional(values, "SOFT_HEDGE_DELTA_CAP_PCT", "0.10")),
        hard_hedge_delta_cap_pct=to_decimal(_optional(values, "HARD_HEDGE_DELTA_CAP_PCT", "0.05")),
        max_concurrent_groups=int(_optional(values, "MAX_CONCURRENT_GROUPS", "3")),
        max_groups_per_currency=int(_optional(values, "MAX_GROUPS_PER_CURRENCY", "2")),
        recovery_normal_cycles=int(_optional(values, "RECOVERY_NORMAL_CYCLES", "3")),
        order_label_prefix=_optional(values, "ORDER_LABEL_PREFIX", "trial"),
        request_timeout_seconds=int(_optional(values, "REQUEST_TIMEOUT_SECONDS", "20")),
        state_file=_to_path(_optional(values, "STATE_FILE", ".state/strategy_state.json")),
        min_net_apr=to_decimal(_optional(values, "MIN_NET_APR", "0.12")),
        target_net_apr_min=to_decimal(_optional(values, "TARGET_NET_APR_MIN", "0.15")),
        target_net_apr_max=to_decimal(_optional(values, "TARGET_NET_APR_MAX", "0.20")),
        btc_put_delta_min=to_decimal(_optional(values, "BTC_PUT_DELTA_MIN", _optional(values, "SHORT_PUT_DELTA_MIN", "0.08"))),
        btc_put_delta_max=to_decimal(_optional(values, "BTC_PUT_DELTA_MAX", _optional(values, "SHORT_PUT_DELTA_MAX", "0.12"))),
        eth_put_delta_min=to_decimal(_optional(values, "ETH_PUT_DELTA_MIN", "0.06")),
        eth_put_delta_max=to_decimal(_optional(values, "ETH_PUT_DELTA_MAX", "0.10")),
        btc_put_otm_min=to_decimal(_optional(values, "BTC_PUT_OTM_MIN", _optional(values, "PUT_OTM_MIN", "0.10"))),
        btc_put_otm_max=to_decimal(_optional(values, "BTC_PUT_OTM_MAX", _optional(values, "PUT_OTM_MAX", "0.18"))),
        eth_put_otm_min=to_decimal(_optional(values, "ETH_PUT_OTM_MIN", "0.12")),
        eth_put_otm_max=to_decimal(_optional(values, "ETH_PUT_OTM_MAX", "0.20")),
        btc_preferred_put_delta_min=to_decimal(
            _optional(values, "BTC_PREFERRED_PUT_DELTA_MIN", _optional(values, "PREFERRED_SHORT_PUT_DELTA_MIN", "0.09"))
        ),
        btc_preferred_put_delta_max=to_decimal(
            _optional(values, "BTC_PREFERRED_PUT_DELTA_MAX", _optional(values, "PREFERRED_SHORT_PUT_DELTA_MAX", "0.11"))
        ),
        eth_preferred_put_delta_min=to_decimal(_optional(values, "ETH_PREFERRED_PUT_DELTA_MIN", "0.07")),
        eth_preferred_put_delta_max=to_decimal(_optional(values, "ETH_PREFERRED_PUT_DELTA_MAX", "0.09")),
        btc_preferred_otm_min=to_decimal(_optional(values, "BTC_PREFERRED_OTM_MIN", "0.12")),
        btc_preferred_otm_max=to_decimal(_optional(values, "BTC_PREFERRED_OTM_MAX", "0.16")),
        eth_preferred_otm_min=to_decimal(_optional(values, "ETH_PREFERRED_OTM_MIN", "0.14")),
        eth_preferred_otm_max=to_decimal(_optional(values, "ETH_PREFERRED_OTM_MAX", "0.18")),
        enable_naked_topup=_to_bool(_optional(values, "ENABLE_NAKED_TOPUP", "false")),
        enable_adopt_exchange_positions=_to_bool(_optional(values, "ENABLE_ADOPT_EXCHANGE_POSITIONS", "true"), default=True),
        enable_short_put=enable_short_put,
        enable_short_call=enable_short_call,
        short_call_delta_min=to_decimal(_optional(values, "SHORT_CALL_DELTA_MIN", "0.08")),
        short_call_delta_max=to_decimal(_optional(values, "SHORT_CALL_DELTA_MAX", "0.14")),
        preferred_short_call_delta_min=to_decimal(_optional(values, "PREFERRED_SHORT_CALL_DELTA_MIN", "0.10")),
        preferred_short_call_delta_max=to_decimal(_optional(values, "PREFERRED_SHORT_CALL_DELTA_MAX", "0.12")),
        call_otm_min=to_decimal(_optional(values, "CALL_OTM_MIN", "0.08")),
        call_otm_max=to_decimal(_optional(values, "CALL_OTM_MAX", "0.18")),
        btc_call_delta_min=to_decimal(_optional(values, "BTC_CALL_DELTA_MIN", "0.08")),
        btc_call_delta_max=to_decimal(_optional(values, "BTC_CALL_DELTA_MAX", "0.12")),
        eth_call_delta_min=to_decimal(_optional(values, "ETH_CALL_DELTA_MIN", "0.06")),
        eth_call_delta_max=to_decimal(_optional(values, "ETH_CALL_DELTA_MAX", "0.10")),
        btc_call_otm_min=to_decimal(_optional(values, "BTC_CALL_OTM_MIN", "0.10")),
        btc_call_otm_max=to_decimal(_optional(values, "BTC_CALL_OTM_MAX", "0.18")),
        eth_call_otm_min=to_decimal(_optional(values, "ETH_CALL_OTM_MIN", "0.12")),
        eth_call_otm_max=to_decimal(_optional(values, "ETH_CALL_OTM_MAX", "0.20")),
        btc_preferred_call_delta_min=to_decimal(_optional(values, "BTC_PREFERRED_CALL_DELTA_MIN", "0.09")),
        btc_preferred_call_delta_max=to_decimal(_optional(values, "BTC_PREFERRED_CALL_DELTA_MAX", "0.11")),
        eth_preferred_call_delta_min=to_decimal(_optional(values, "ETH_PREFERRED_CALL_DELTA_MIN", "0.07")),
        eth_preferred_call_delta_max=to_decimal(_optional(values, "ETH_PREFERRED_CALL_DELTA_MAX", "0.09")),
        btc_preferred_call_otm_min=to_decimal(_optional(values, "BTC_PREFERRED_CALL_OTM_MIN", "0.12")),
        btc_preferred_call_otm_max=to_decimal(_optional(values, "BTC_PREFERRED_CALL_OTM_MAX", "0.16")),
        eth_preferred_call_otm_min=to_decimal(_optional(values, "ETH_PREFERRED_CALL_OTM_MIN", "0.14")),
        eth_preferred_call_otm_max=to_decimal(_optional(values, "ETH_PREFERRED_CALL_OTM_MAX", "0.18")),
        bull_put_long_delta_min=to_decimal(_optional(values, "BULL_PUT_LONG_DELTA_MIN", "0.02")),
        bull_put_long_delta_max=to_decimal(_optional(values, "BULL_PUT_LONG_DELTA_MAX", "0.05")),
        per_leg_im_cap_put=to_decimal(_optional(values, "PER_LEG_IM_CAP_PUT", "0.15")),
        per_leg_im_cap_call=to_decimal(_optional(values, "PER_LEG_IM_CAP_CALL", "0.12")),
        expiry_im_cap_per_book=to_decimal(_optional(values, "EXPIRY_IM_CAP", "0.30")),
        book_im_target=to_decimal(_optional(values, "BOOK_IM_TARGET", "0.35")),
        book_im_hard=book_im_hard_value,
        book_mm_target=to_decimal(_optional(values, "BOOK_MM_TARGET", "0.22")),
        book_mm_hard=to_decimal(_optional(values, "BOOK_MM_HARD", "0.33")),
        short_call_fallback_only=short_call_fallback_only,
        entry_cooldown_minutes=int(_optional(values, "ENTRY_COOLDOWN_MINUTES", "20")),
        reprice_minutes=int(_optional(values, "REPRICE_MINUTES", "3")),
        inverse_min_open_interest=inverse_min_open_interest,
        btc_inverse_min_open_interest=btc_inverse_min_open_interest,
        eth_inverse_min_open_interest=eth_inverse_min_open_interest,
        inverse_max_spread_ratio=to_decimal(
            _optional(values, "INVERSE_MAX_SPREAD_RATIO", _optional(values, "MAX_SPREAD_RATIO", "0.12"))
        ),
        inverse_min_book_notional_usdc=to_decimal(
            _optional(values, "INVERSE_MIN_BOOK_NOTIONAL_USDC", _optional(values, "MIN_BOOK_NOTIONAL_USDC", "3000"))
        ),
        linear_min_open_interest=linear_min_open_interest,
        btc_linear_min_open_interest=btc_linear_min_open_interest,
        eth_linear_min_open_interest=eth_linear_min_open_interest,
        linear_max_spread_ratio=to_decimal(_optional(values, "LINEAR_MAX_SPREAD_RATIO", "0.14")),
        linear_min_book_notional_usdc=to_decimal(
            _optional(values, "LINEAR_MIN_BOOK_NOTIONAL_USDC", "4000")
        ),
        max_groups_per_book=int(
            _optional(values, "MAX_GROUPS_PER_BOOK", _optional(values, "MAX_GROUPS_PER_CURRENCY", "3"))
        ),
        soft_defense_delta_call=to_decimal(
            _optional(values, "SOFT_DEFENSE_DELTA_CALL", _optional(values, "SOFT_DEFENSE_DELTA", "0.18"))
        ),
        hard_defense_delta_call=to_decimal(
            _optional(values, "HARD_DEFENSE_DELTA_CALL", _optional(values, "HARD_DEFENSE_DELTA", "0.24"))
        ),
        scan_assets=scan_assets,
        scan_underlyings=scan_underlyings,
        traded_collaterals=traded_collaterals,
        min_book_equity_usdc=min_book_equity_usdc,
        cash_flow_query_interval_seconds=cash_flow_query_interval_seconds,
        covered_call_spot_exit_enabled=_to_bool(
            _optional(values, "COVERED_CALL_SPOT_EXIT_ENABLED", "false")
        ),
        covered_call_robust_exit_enabled=_to_bool(
            _optional(values, "COVERED_CALL_ROBUST_EXIT_ENABLED", "false")
        ),
        covered_call_robust_exit_dte=to_decimal(
            _optional(values, "COVERED_CALL_ROBUST_EXIT_DTE", "0.5")
        ),
        covered_call_itm_buffer_pct=to_decimal(
            _optional(values, "COVERED_CALL_ITM_BUFFER_PCT", "0")
        ),
        covered_call_spot_order_type=covered_call_spot_order_type,
    )
