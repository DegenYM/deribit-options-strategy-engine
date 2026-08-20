from cc_saas.onboarding import parse_intake, recommend
from conftest import approve, make_client, signup

ACKS = ["not_advice", "no_apr", "spot_downside", "keys_own", "panic_no_fill"]


def _intake(**overrides):
    payload = {
        "experience": "options",
        "inventory": "already_on_deribit",
        "coins": "ETH",
        "capital_band": "10_50k",
        "intent": "overlay",
        "drawdown": "balanced",
        "want_sweep": False,
        "alerts": False,
        "acknowledgements": ACKS,
    }
    payload.update(overrides)
    return payload


def test_recommend_novice_without_spot_gets_scout():
    rec = recommend(
        parse_intake(
            _intake(
                experience="novice",
                inventory="none",
                coins="both",
                intent="learn",
                drawdown="aggressive",
                want_sweep=True,
            )
        )
    )
    assert rec.plan_id == "scout"
    assert rec.coins == ("BTC",)
    assert rec.risk_tier == "low"
    assert rec.profit_sweep is False


def test_recommend_overlay_single_coin_trader():
    rec = recommend(parse_intake(_intake()))
    assert rec.plan_id == "trader"
    assert rec.coins == ("ETH",)
    assert rec.risk_tier == "medium"


def test_recommend_both_coins_pro():
    rec = recommend(parse_intake(_intake(coins="both", drawdown="aggressive", want_sweep=True)))
    assert rec.plan_id == "pro"
    assert rec.coins == ("BTC", "ETH")
    assert rec.risk_tier == "high"
    assert rec.profit_sweep is True


def test_recommend_desk_intent():
    rec = recommend(parse_intake(_intake(intent="desk", coins="BTC", experience="novice")))
    assert rec.plan_id == "desk"


def test_intake_requires_acknowledgements():
    client = make_client()
    signup(client, "ack@example.com")
    res = client.post("/api/onboarding", json=_intake(acknowledgements=["not_advice"]))
    assert res.status_code == 400


def test_settings_blocked_until_intake():
    client = make_client()
    signup(client, "blocked@example.com")
    approve("blocked@example.com")
    client.post("/api/billing/dev-subscribe", json={"plan_id": "trader"})
    res = client.post(
        "/api/bot/settings",
        json={"risk_tier": "medium", "coins": ["ETH"], "profit_sweep": False},
    )
    assert res.status_code == 400
    saved = client.post("/api/onboarding", json=_intake())
    assert saved.status_code == 200
    assert saved.json()["recommendation"]["plan_id"] == "trader"
    ok = client.post(
        "/api/bot/settings",
        json={"risk_tier": "medium", "coins": ["ETH"], "profit_sweep": False},
    )
    assert ok.status_code == 200


def test_product_meta_and_schema():
    client = make_client()
    product = client.get("/api/product").json()
    assert product["brand"] == "Canopy"
    assert product["strategy"] == "covered_call"
    assert "代操" in product["not_claims_zh"]
    schema = client.get("/api/onboarding/schema").json()
    assert schema["questions"][0]["id"] == "experience"
