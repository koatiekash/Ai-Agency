"""Compliance: deterministic rules first, then the business's compliance agent.

Hard rules are code and cannot be talked around. The compliance agent then reviews
anything that passed for judgment calls (misleading claims, tone, policy fit).
An action must pass both. Failures go back to the originating executive with the
reasons, get revised, and are checked again, up to MAX_ATTEMPTS.
"""
from __future__ import annotations

import os
import re

from . import mailer
from .realestate import deals as re_tools
from .trading import engine as tr_engine
from .trading.risk import RiskLimits, check_order

MAX_ATTEMPTS = int(os.environ.get("COMPLIANCE_MAX_ATTEMPTS", 3))

BANNED_CLAIMS = [
    r"\bguarantee[ds]?\b", r"\brisk[- ]free\b", r"\bcure[sd]?\b", r"\bmiracle\b", r"\bclinically proven\b",
    r"\bdoctor recommended\b", r"#1\b", r"\bbest seller\b", r"\bget rich\b", r"\bpassive income\b",
    r"\bcan'?t lose\b", r"\b100% (profit|win)", r"\bFDA approved\b",
]
TIKTOK_PROHIBITED = ["weight loss", "supplement", "cbd", "vape", "weapon", "knife", "gun", "medical device",
                     "contact lens", "pesticide", "lithium battery pack"]
FAIR_HOUSING_TERMS = ["no kids", "adults only", "christian", "muslim", "jewish", "hispanic", "white neighborhood",
                      "black neighborhood", "immigrant", "disabled", "handicap", "family-oriented", "bachelor"]


def _claims(text: str) -> list[str]:
    t = (text or "").lower()
    return [f"Prohibited claim matches '{p}'." for p in BANNED_CLAIMS if re.search(p, t, re.I)]


def hard_rules(business: str, a: dict, ctx: dict) -> list[str]:
    v: list[str] = []
    msg = (a.get("message") or "")
    v += _claims(msg)

    if business == "trading":
        limits = RiskLimits()
        spec = tr_engine.spec(a.get("symbol") or os.environ.get("TRADING_SYMBOL", "US500"))
        order = {"action": a["action"], "symbol": a.get("symbol"), "contracts": a.get("contracts") or 0,
                 "entry": a.get("entry"), "stop": a.get("stop")}
        v += check_order(order, ctx.get("account", {}), ctx.get("strategy_oos", {}), limits, spec["point_value"])
        if a["action"] == "promote_strategy" and not ctx.get("walk_forward", {}).get("promote"):
            v.append("Promotion requires the walk-forward test to show out-of-sample improvement.")

    elif business == "wholesale":
        lead = next((l for l in ctx.get("leads", []) if str(l.get("id")) == str(a.get("target"))), None)
        if a["action"] in ("make_offer", "send_outreach", "schedule_appointment") and lead is None:
            v.append(f"Unknown lead '{a.get('target')}'.")
        if lead:
            state = (lead.get("state") or "").upper()
            if state not in re_tools.states_cleared():
                v.append(f"State {state or '?'} is outside the operating footprint (WHOLESALE_STATES_CLEARED).")
            rule = re_tools.STATE_RULES.get(state, {})
            licensed = state in re_tools.states_licensed()
            if a["action"] == "market_deal_to_buyers" and rule.get("license_to_market") and not licensed:
                v.append(f"{state}: publicly marketing a contract requires a license. {rule.get('note', '')}")
            cap = rule.get("max_unlicensed_12mo")
            if cap is not None and not licensed and a["action"] in ("make_offer", "market_deal_to_buyers") \
                    and ctx.get("deals_12mo", {}).get(state, 0) >= cap:
                v.append(f"{state}: unlicensed limit of {cap} wholesale deal(s) per 12 months reached.")
            if rule.get("equitable_interest") and a["action"] in ("make_offer", "send_outreach", "market_deal_to_buyers") \
                    and msg and "equitable interest" not in msg.lower():
                v.append(f"{state}: message must disclose that you hold only an equitable interest in the contract.")
            if state == "FL" and a["action"] == "market_deal_to_buyers" and msg \
                    and "assignment" not in msg.lower():
                v.append("FL: market the assignment of your contract, not the property itself.")
            if a["action"] == "make_offer":
                deal = re_tools.underwrite(lead)
                if a.get("amount") and a["amount"] > deal["mao"]:
                    v.append(f"Offer ${a['amount']:,.0f} exceeds MAO ${deal['mao']:,.0f}.")
            ch = (a.get("channel") or "").lower()
            if ch in ("call", "sms", "text"):
                if str(lead.get("dnc", "")).lower() in ("", "unknown"):
                    v.append("Phone not scrubbed against the DNC registry (dnc column empty).")
                elif str(lead.get("dnc")).lower() in ("yes", "true", "1"):
                    v.append("Number is on a Do-Not-Call list.")
                if not re_tools.contact_window_ok(lead.get("timezone")):
                    v.append("Outside 8am-9pm recipient-local calling hours.")
                if ch in ("sms", "text") and str(lead.get("consent", "")).lower() not in ("yes", "true", "1"):
                    v.append("No prior express written consent on file for marketing texts.")
            if a["action"] in ("send_outreach", "make_offer") and msg:
                low = msg.lower()
                if "assign" not in low:
                    v.append("Message must disclose that the contract may be assigned.")
                if "not a licensed" not in low and "not licensed" not in low:
                    v.append("Message must state you are not a licensed real estate agent.")
                if ch == "email":
                    if mailer.configured():   # the mailer appends address + unsubscribe line itself
                        v += [p for p in mailer.can_send(lead.get("email", "")) if "not configured" not in p]
                    elif "unsubscribe" not in low:
                        v.append("Email needs an unsubscribe line (CAN-SPAM).")
        if any(t in msg.lower() for t in FAIR_HOUSING_TERMS):
            v.append("Language may violate the Fair Housing Act.")

    elif business == "tiktok":
        target = (a.get("target") or "").lower()
        if any(p in target or p in msg.lower() for p in TIKTOK_PROHIBITED):
            v.append("Prohibited or restricted TikTok Shop category.")
        if a["action"] in ("shortlist_product", "draft_listing", "request_supplier_samples"):
            scored = next((p for p in ctx.get("products", []) if (p.get("product") or "").lower() == target), None)
            if scored is None:
                v.append(f"Product '{a.get('target')}' is not in the scored research set.")
            elif not scored.get("passes_margin_floor"):
                v.append(f"Margin {scored['margin']:.0%} is below the floor.")
        if a["action"] == "recruit_affiliate_creators" and msg and "#ad" not in msg.lower() \
                and "paid partnership" not in msg.lower():
            v.append("Creator brief must require #ad / Paid-partnership disclosure.")

    elif business == "marketing":
        disc = " ".join(a.get("disclosures") or []).lower() + " " + msg.lower()
        if ctx.get("for_business") == "trading" and "risk" not in disc:
            v.append("Trading content needs a futures risk disclaimer.")
        if ctx.get("for_business") == "tiktok" and ("#ad" not in disc and "paid partnership" not in disc
                                                    and "affiliate" not in disc):
            v.append("Shop content needs an #ad / affiliate disclosure.")
        if any(t in msg.lower() for t in FAIR_HOUSING_TERMS):
            v.append("Language may violate the Fair Housing Act.")

    budget = float(os.environ.get(f"{business.upper()}_MAX_SPEND", 0) or 0)
    if budget and (a.get("amount") or 0) > budget and a.get("action") in ("request_marketing_campaign",):
        v.append(f"Spend ${a['amount']:,.0f} exceeds the ${budget:,.0f} limit.")
    return v
