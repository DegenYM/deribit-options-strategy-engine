from cc_saas.covered_call_pnl import book_usd, overlay_coin, overlay_usd, settlement_coin


def test_otm_keeps_premium_in_coin():
    coin = overlay_coin(expiry=105_000, strike=110_000, premium_coin=0.015)
    assert coin == 0.015
    assert overlay_usd(coin_pnl=coin, expiry=105_000) == 0.015 * 105_000
    assert settlement_coin(expiry=90_000, strike=110_000) == 0


def test_itm_settles_in_coin():
    coin = overlay_coin(expiry=130_000, strike=110_000, premium_coin=0.015)
    assert round(coin, 6) == round(0.015 - 20_000 / 130_000, 6)
    usd = overlay_usd(coin_pnl=coin, expiry=130_000)
    assert usd < 0


def test_crash_coin_still_premium_usd_book_follows_spot():
    coin = overlay_coin(expiry=50_000, strike=110_000, premium_coin=0.015)
    assert coin == 0.015
    book = book_usd(coin_pnl=coin, expiry=50_000, entry_spot=100_000)
    assert book == 1.015 * 50_000 - 100_000
