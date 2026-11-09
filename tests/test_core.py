import os
import tempfile

os.environ["AGENCY_OFFLINE"] = "1"
os.environ["AGENCY_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

from agency.compliance import hard_rules  # noqa: E402
from agency.realestate import deals  # noqa: E402
from agency.tiktok import products  # noqa: E402
from agency.trading import engine as tr  # noqa: E402
from agency.trading.risk import RiskLimits, check_order  # noqa: E402

GOOD_OOS = {"trades": 40, "avg_sharpe": 1.0, "data_source": "csv"}
ACCOUNT = {"equity": 10000, "peak_equity": 10000, "daily_pnl": 0}


def test_mao_follows_70_percent_rule():
    d = deals.underwrite({"est_value": 200000, "sqft": 1000, "condition": "fair"}, fee=10000)
    assert d["repairs"] == 25000
    assert d["buyer_max_price"] == 200000 * 0.7 - 25000
    assert d["mao"] == d["buyer_max_price"] - 10000


def test_risk_blocks_missing_stop_and_oversize():
    v = check_order({"action": "enter_long", "symbol": "US500", "contracts": 9, "entry": 5000, "stop": None},
                    ACCOUNT, GOOD_OOS, RiskLimits(), 5.0)
    assert any("stop" in x for x in v) and any("Size" in x for x in v)


def test_fractional_lots_allowed_for_cfds():
    order = {"action": "enter_long", "symbol": "US500", "contracts": 0.1, "entry": 5000, "stop": 4990}
    assert check_order(order, ACCOUNT, GOOD_OOS, RiskLimits(), 1.0) == []


def test_risk_passes_valid_order_and_blocks_after_daily_loss():
    order = {"action": "enter_long", "symbol": "MES", "contracts": 1, "entry": 5000, "stop": 4990}
    assert check_order(order, ACCOUNT, GOOD_OOS, RiskLimits(), 5.0) == []
    assert check_order(order, {**ACCOUNT, "daily_pnl": -1000}, GOOD_OOS, RiskLimits(), 5.0)
    assert check_order(order, {**ACCOUNT, "kill_switch": True}, GOOD_OOS, RiskLimits(), 5.0)


def test_risk_refuses_strategy_validated_on_synthetic_data():
    order = {"action": "enter_short", "symbol": "MES", "contracts": 1, "entry": 5000, "stop": 5010}
    v = check_order(order, ACCOUNT, {**GOOD_OOS, "data_source": "synthetic"}, RiskLimits(), 5.0)
    assert any("synthetic" in x for x in v)


def test_wholesale_compliance_requires_disclosures_and_dnc():
    lead = {**deals.SAMPLE_LEADS[0], "dnc": ""}
    a = {"action": "send_outreach", "target": lead["id"], "channel": "call", "message": "We buy houses fast!",
         "amount": None}
    v = hard_rules("wholesale", a, {"leads": [lead]})
    joined = " ".join(v)
    assert "assign" in joined and "licensed" in joined and "DNC" in joined
    assert "outside the operating footprint" not in joined          # OH is in the default footprint
    tx = {**lead, "state": "TX"}
    assert any("footprint" in x for x in hard_rules("wholesale", a, {"leads": [tx]}))


def test_illinois_needs_license_to_market_and_caps_unlicensed_deals():
    lead = {**deals.SAMPLE_LEADS[0], "state": "IL"}
    msg = "Assignment of contract; I hold only an equitable interest and may assign. I am not a licensed agent."
    a = {"action": "market_deal_to_buyers", "target": lead["id"], "channel": "email", "message": msg, "amount": None}
    assert any("requires a license" in x for x in hard_rules("wholesale", a, {"leads": [lead]}))
    os.environ["WHOLESALE_LICENSED_STATES"] = "IL"
    try:
        assert hard_rules("wholesale", a, {"leads": [lead]}) == []
    finally:
        del os.environ["WHOLESALE_LICENSED_STATES"]
    offer = {**a, "action": "make_offer", "channel": "mail", "amount": 1000}
    assert any("per 12 months" in x for x in hard_rules("wholesale", offer, {"leads": [lead], "deals_12mo": {"IL": 1}}))


def test_florida_markets_the_assignment_not_the_property():
    lead = deals.SAMPLE_LEADS[2]
    assert lead["state"] == "FL"
    a = {"action": "market_deal_to_buyers", "target": lead["id"], "channel": "mail", "amount": None,
         "message": "Great house for sale! I may assign. I am not a licensed agent."}
    assert any("FL:" in x for x in hard_rules("wholesale", a, {"leads": [lead]}))


def test_mailer_blocks_unsubscribed_and_missing_address(tmp_path, monkeypatch):
    from agency import mailer
    monkeypatch.setattr(mailer, "SUPPRESS", str(tmp_path / "u.csv"))
    monkeypatch.setattr(mailer, "SENT_LOG", str(tmp_path / "s.csv"))
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com"); monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.delenv("BUSINESS_ADDRESS", raising=False)
    mailer._suppress("seller@example.com")
    v = mailer.can_send("seller@example.com")
    assert any("unsubscribed" in x for x in v) and any("BUSINESS_ADDRESS" in x for x in v)


def test_tiktok_is_paused_by_default():
    from agency.org import paused_businesses
    assert "tiktok" in paused_businesses()


def test_offer_above_mao_is_blocked():
    lead = deals.SAMPLE_LEADS[0]
    mao = deals.underwrite(lead)["mao"]
    a = {"action": "make_offer", "target": lead["id"], "channel": "mail", "amount": mao + 5000,
         "message": "I may assign this contract. I am not a licensed real estate agent."}
    assert any("exceeds MAO" in x for x in hard_rules("wholesale", a, {"leads": [lead]}))


def test_tiktok_blocks_claims_prohibited_and_low_margin():
    ranked, _ = products.ranked()
    a = {"action": "draft_listing", "target": "Herbal weight loss tea", "message": "Guaranteed results!"}
    v = hard_rules("tiktok", a, {"products": ranked})
    assert any("Prohibited" in x for x in v) and any("guarantee" in x.lower() for x in v)


def test_backtest_and_walk_forward_run():
    df = tr.synthetic_bars("US500", 1200)
    m = tr.backtest(df, tr.Params(), "US500")
    assert set(m) >= {"trades", "win_rate", "sharpe", "max_drawdown"}
    wf = tr.walk_forward(df, "US500", tr.Params(), folds=2)
    assert "promote" in wf and wf["oos_challenger"]["trades"] >= 0


def test_offline_cycle_runs_end_to_end():
    from agency.orchestrator import run_cycle
    from agency.store import CommandCenter
    cc = CommandCenter(os.environ["AGENCY_DB"])
    out = run_cycle(cc, only=["wholesale", "tiktok"])
    assert [u["business"] for u in out["updates"]] == ["wholesale"]      # tiktok is paused
    assert all("error" not in u for u in out["updates"])
    assert cc.history(kind="briefing")
