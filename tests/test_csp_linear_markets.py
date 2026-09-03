"""Cash-secured puts are linear USDC options on an inverse_native account.

covered_call runs with OPTION_MARKETS_PROFILE=inverse_native, which rejects
every USDC-quoted+settled market. The CSP leg therefore must not depend on
`_load_supported_option_markets`; it has its own loader, and open CSP positions
resolve their metadata through the per-instrument fallback.
"""

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot

_USDC_PUT = {
    "instrument_name": "BTC_USDC-28MAR25-80000-P",
    "base_currency": "BTC",
    "quote_currency": "USDC",
    "settlement_currency": "USDC",
    "option_type": "put",
    "strike": 80000,
    "expiration_timestamp": 4102444800000,
    "creation_timestamp": 0,
    "is_active": True,
    "contract_size": 1,
    "min_trade_amount": 1,
    "tick_size": 0.0001,
}


class _UsdcChainClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.instrument_calls: list[tuple[str, str]] = []
        self.single_lookups: list[str] = []

    def get_instruments(self, currency, *, kind="option", expired=False):
        self.instrument_calls.append((currency.upper(), kind))
        if currency.upper() == "USDC" and kind == "option":
            return [_USDC_PUT]
        return super().get_instruments(currency, kind=kind, expired=expired)

    def get_instrument(self, instrument_name):
        self.single_lookups.append(instrument_name)
        if instrument_name == _USDC_PUT["instrument_name"]:
            return dict(_USDC_PUT)
        return super().get_instrument(instrument_name)


def _covered_call_engine(tmp_path):
    return DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            managed_currencies=("BTC", "ETH"),
        ),
        _UsdcChainClient(),
    )


def test_csp_put_loader_still_sees_linear_usdc_chain(tmp_path):
    """The CSP loader bypasses the profile filter deliberately."""
    engine = _covered_call_engine(tmp_path)

    puts = engine._load_linear_usdc_puts("BTC")

    assert [p.instrument_name for p in puts] == [_USDC_PUT["instrument_name"]]
    assert ("USDC", "option") in engine.client.instrument_calls


def test_open_csp_position_resolves_metadata_despite_profile_filter(tmp_path):
    """inverse_native keeps linear puts out of markets_by_currency (it did so
    before and after the skip), so an open CSP resolves via per-instrument
    lookup rather than the chain."""
    engine = _covered_call_engine(tmp_path)
    markets = engine._load_supported_option_markets()

    names = {m.instrument_name for rows in markets.values() for m in rows}
    assert _USDC_PUT["instrument_name"] not in names

    resolved = engine._find_or_fetch_instrument(markets, _USDC_PUT["instrument_name"])

    assert resolved.instrument_name == _USDC_PUT["instrument_name"]
    assert engine.client.single_lookups == [_USDC_PUT["instrument_name"]]
