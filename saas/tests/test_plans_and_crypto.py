from datetime import UTC, datetime, timedelta

import pytest
from cc_saas.crypto import decrypt_secret, encrypt_secret, last4
from cc_saas.entitlements import can_use_coins, can_use_tier, live_unlocked
from cc_saas.models import User
from cc_saas.plans import get_plan, public_catalog


def test_public_catalog_is_covered_call_only():
    plans = public_catalog()
    assert [row["id"] for row in plans] == ["scout", "trader", "pro", "desk"]
    assert all(row["strategy"] == "covered_call" for row in plans)
    assert all("不是收益承諾" in row["disclaimer_zh"] or "收益承諾" in row["disclaimer_zh"] for row in plans)
    scout = get_plan("scout")
    assert scout.live_trading is False
    assert scout.allowed_tiers == ("low",)
    public = public_catalog()
    assert public[0]["trial_eligible"] is True
    assert public[1]["trial_eligible"] is False
    trader = get_plan("trader")
    assert trader.live_trading is True
    assert trader.coins_max == 1
    pro = get_plan("pro")
    assert pro.profit_sweep is True
    assert pro.coins_max == 2


def test_entitlements_reject_high_tier_on_trader():
    trader = get_plan("trader")
    assert can_use_tier(trader, "medium") is True
    assert can_use_tier(trader, "high") is False
    assert can_use_coins(trader, ["BTC"]) is True
    assert can_use_coins(trader, ["BTC", "ETH"]) is False


def test_live_requires_dry_run_window():
    plan = get_plan("trader")
    user = User(email="a@b.co")
    assert live_unlocked(user, plan, now=datetime.now(tz=UTC)) is False
    user.paper_started_at = datetime.now(tz=UTC) - timedelta(days=3)
    assert live_unlocked(user, plan, now=datetime.now(tz=UTC)) is False
    user.paper_started_at = datetime.now(tz=UTC) - timedelta(days=8)
    assert live_unlocked(user, plan, now=datetime.now(tz=UTC)) is True


def test_scout_never_live():
    user = User(email="a@b.co", paper_started_at=datetime.now(tz=UTC) - timedelta(days=30))
    assert live_unlocked(user, get_plan("scout"), now=datetime.now(tz=UTC)) is False


def test_encrypt_roundtrip_and_last4():
    token = encrypt_secret("super-secret-key")
    assert "super-secret-key" not in token
    assert decrypt_secret(token) == "super-secret-key"
    assert last4("abcd1234") == "1234"


def test_unknown_plan_raises():
    with pytest.raises(KeyError):
        get_plan("naked")
