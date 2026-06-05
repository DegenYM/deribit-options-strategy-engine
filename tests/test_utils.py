from decimal import Decimal

from deribit_engine.utils import align_option_order_amount, dumps_json


def test_align_option_order_amount_uses_smaller_step_than_contract_size():
    assert align_option_order_amount(Decimal("0.6"), Decimal("1"), Decimal("0.1")) == Decimal("0.6")
    assert align_option_order_amount(Decimal("0.05"), Decimal("1"), Decimal("0.1")) == Decimal("0")


def test_dumps_json_preserves_unicode_text():
    rendered = dumps_json({"note": "目前是 crisis 風控狀態"})

    assert "目前是 crisis 風控狀態" in rendered
    assert "\\u76ee" not in rendered


def test_parse_exchange_price_band_limit():
    from deribit_engine.utils import parse_exchange_price_band_limit

    assert parse_exchange_price_band_limit("price_too_high 2180.0") == Decimal("2180.0")
    assert parse_exchange_price_band_limit('private/buy failed: ... "message":"price_too_low 5.0"') == Decimal("5.0")
    assert parse_exchange_price_band_limit("insufficient_funds") is None


def test_is_post_only_reject():
    from deribit_engine.exceptions import ExchangeError
    from deribit_engine.utils import is_post_only_reject

    err = ExchangeError('private/sell failed: HTTP 400 {"error":{"code":11054,"message":"post_only_reject"}}')
    assert is_post_only_reject(err)
    assert not is_post_only_reject(ExchangeError("insufficient_funds"))
