from decimal import Decimal

from deribit_engine.utils import align_option_order_amount, dumps_json


def test_align_option_order_amount_uses_smaller_step_than_contract_size():
    assert align_option_order_amount(Decimal("0.6"), Decimal("1"), Decimal("0.1")) == Decimal("0.6")
    assert align_option_order_amount(Decimal("0.05"), Decimal("1"), Decimal("0.1")) == Decimal("0")


def test_dumps_json_preserves_unicode_text():
    rendered = dumps_json({"note": "目前是 crisis 風控狀態"})

    assert "目前是 crisis 風控狀態" in rendered
    assert "\\u76ee" not in rendered
