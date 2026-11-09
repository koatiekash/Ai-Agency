"""Wholesale tools: lead loading, deal math, buyer matching and outreach rules.

Data sources (first one available wins):
  1. ATTOM API, if ATTOM_API_KEY is set (property detail + AVM).
  2. CSV exports in data/leads.csv (PropStream, BatchLeads, DealMachine, REsimpli…).
  3. A small built-in sample so the pipeline runs on day one (marked "sample").
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")

# Operating footprint: the 12 Midwest states plus Florida.
MIDWEST = ("IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI")
DEFAULT_STATES = MIDWEST + ("FL",)

# Per-state rules the compliance analyst enforces. Compiled from secondary sources (Nov 2026);
# statutes change, so have a local real estate attorney confirm each state you work in.
#   license_to_market:   publicly marketing the contract needs a license -> blocked unless licensed
#   max_unlicensed_12mo: wholesale deals allowed per 12 months without a license
#   equitable_interest:  outreach/offers must say you hold only an equitable interest / contract rights
STATE_RULES: dict[str, dict] = {
    "IL": {"max_unlicensed_12mo": 1, "license_to_market": True, "equitable_interest": True,
           "note": "More than one wholesale deal in 12 months requires a broker license or wholesaler "
                   "registration; fines up to $25,000 per offense."},
    "NE": {"license_to_market": True, "equitable_interest": True,
           "note": "A license is generally required to publicly market an equitable interest (LB 860, 2024)."},
    "WI": {"max_unlicensed_12mo": 1, "license_to_market": True, "equitable_interest": True,
           "note": "A pattern of sales makes you a broker under Ch. 452; marketing needs a license."},
    "ND": {"equitable_interest": True, "note": "Disclosure required (HB 1125, effective Aug 1, 2025)."},
    "OH": {"equitable_interest": True, "note": "Disclosure requirements added 2025-2026."},
    "IA": {"equitable_interest": True, "note": "Disclosure required; some sources say a license may be needed."},
    "MI": {"equitable_interest": True, "note": "HB 5366 (Dec 2025) would make wholesaling licensed activity; "
                                               "check its status before scaling."},
    "FL": {"note": "No wholesaling statute; assign only your own contract and market the contract, never the "
                   "property itself (Ch. 475 brokerage rules)."},
}
for _st in DEFAULT_STATES:
    STATE_RULES.setdefault(_st, {"note": "No wholesaling-specific statute found; standard disclosures apply."})


def states_cleared() -> set[str]:
    raw = os.environ.get("WHOLESALE_STATES_CLEARED")
    states = raw.split(",") if raw is not None else DEFAULT_STATES
    return {x.strip().upper() for x in states if x.strip()}


def states_licensed() -> set[str]:
    return {x.strip().upper() for x in os.environ.get("WHOLESALE_LICENSED_STATES", "").split(",") if x.strip()}


SAMPLE_LEADS = [
    {"id": "S1", "address": "1214 Elm St", "city": "Columbus", "state": "OH", "zip": "43207", "owner": "J. Rivera",
     "beds": 3, "baths": 1, "sqft": 1240, "year_built": 1958, "asking": 0, "est_value": 210000,
     "condition": "fair", "signals": "absentee;tax-delinquent;high-equity", "phone": "", "email": "",
     "dnc": "", "consent": "", "timezone": "America/New_York", "source": "sample"},
    {"id": "S2", "address": "88 Birch Ave", "city": "Indianapolis", "state": "IN", "zip": "46218", "owner": "M. Chen",
     "beds": 3, "baths": 2, "sqft": 1500, "year_built": 1972, "asking": 185000, "est_value": 245000,
     "condition": "poor", "signals": "vacant;pre-foreclosure", "phone": "", "email": "",
     "dnc": "", "consent": "", "timezone": "America/Indiana/Indianapolis", "source": "sample"},
    {"id": "S3", "address": "402 Cedar Ln", "city": "Tampa", "state": "FL", "zip": "33610", "owner": "Estate of R. Lee",
     "beds": 2, "baths": 1, "sqft": 980, "year_built": 1949, "asking": 0, "est_value": 160000,
     "condition": "poor", "signals": "probate;vacant", "phone": "", "email": "",
     "dnc": "", "consent": "", "timezone": "America/New_York", "source": "sample"},
]

SAMPLE_BUYERS = [
    {"name": "Sample Cash Buyer A", "zips": "43207;46218", "max_price": 150000, "min_beds": 2,
     "strategy": "flip", "proof_of_funds": "yes", "email": "", "source": "sample"},
    {"name": "Sample Landlord B", "zips": "33610;43207", "max_price": 120000, "min_beds": 2,
     "strategy": "rental", "proof_of_funds": "no", "email": "", "source": "sample"},
]


def _read_csv(name: str) -> list[dict]:
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k.strip().lower(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _attom(path: str, params: dict) -> dict | None:
    key = os.environ.get("ATTOM_API_KEY")
    if not key:
        return None
    url = f"https://api.gateway.attomdata.com/propertyapi/v1.0.0/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"apikey": key, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:  # network/auth errors are reported, not fatal
        return {"error": str(e)}


def load_leads(limit: int = 15) -> tuple[list[dict], str]:
    rows = _read_csv("leads.csv")
    if rows:
        for i, r in enumerate(rows):
            r.setdefault("id", str(i + 1)); r["source"] = r.get("source") or "csv"
        return rows[:limit], "csv"
    zips = [z for z in os.environ.get("WHOLESALE_ZIPS", "").split(",") if z.strip()]
    if os.environ.get("ATTOM_API_KEY") and zips:
        leads = []
        for z in zips:
            data = _attom("property/snapshot", {"postalcode": z.strip(), "pagesize": 25,
                                                "propertytype": "SFR"}) or {}
            for p in data.get("property", [])[: limit]:
                a = p.get("address", {})
                leads.append({"id": str(p.get("identifier", {}).get("attomId")),
                              "address": a.get("line1"), "city": a.get("locality"),
                              "state": a.get("countrySubd"), "zip": a.get("postal1"),
                              "beds": p.get("building", {}).get("rooms", {}).get("beds"),
                              "sqft": p.get("building", {}).get("size", {}).get("universalsize"),
                              "est_value": (p.get("avm", {}) or {}).get("amount", {}).get("value"),
                              "signals": "", "source": "attom"})
        if leads:
            return leads[:limit], "attom"
    return SAMPLE_LEADS, "sample"


def load_buyers() -> tuple[list[dict], str]:
    rows = _read_csv("buyers.csv")
    return (rows, "csv") if rows else (SAMPLE_BUYERS, "sample")


# ------------------------------- deal math --------------------------------- #
REPAIR_PER_SQFT = {"good": 10, "fair": 25, "poor": 45, "gut": 70}


def underwrite(lead: dict, arv: float | None = None, repairs: float | None = None,
               fee: float = float(os.environ.get("WHOLESALE_MIN_FEE", 10000)),
               buyer_rule: float = 0.70) -> dict:
    """Standard 70% rule: buyer pays at most ARV*0.70 - repairs; our MAO subtracts our fee."""
    arv = float(arv or lead.get("arv") or lead.get("est_value") or 0)
    sqft = float(lead.get("sqft") or 1200)
    repairs = float(repairs if repairs is not None else sqft * REPAIR_PER_SQFT.get(lead.get("condition", "fair"), 25))
    buyer_max = arv * buyer_rule - repairs
    mao = buyer_max - fee
    asking = float(lead.get("asking") or 0)
    return {
        "lead_id": lead.get("id"), "arv": round(arv), "repairs": round(repairs),
        "buyer_max_price": round(buyer_max), "assignment_fee": round(fee),
        "mao": round(mao), "opening_offer": round(mao * 0.9),
        "asking": round(asking), "viable": mao > 0 and (asking == 0 or asking <= mao),
        "buyer_profit_est": round(arv - buyer_max - repairs - arv * 0.10),  # minus ~10% hold/sell costs
    }


def match_buyers(lead: dict, deal: dict, buyers: list[dict]) -> list[str]:
    out = []
    for b in buyers:
        zips = {z.strip() for z in str(b.get("zips", "")).replace(",", ";").split(";") if z.strip()}
        if zips and str(lead.get("zip")) not in zips:
            continue
        if float(b.get("max_price") or 1e12) < deal["buyer_max_price"]:
            continue
        if int(float(b.get("min_beds") or 0)) > int(float(lead.get("beds") or 0)):
            continue
        out.append(b["name"])
    return out


# ---------------------------- outreach rules ------------------------------- #
def contact_window_ok(tz: str | None, now: dt.datetime | None = None) -> bool:
    """TCPA: no telemarketing calls/texts before 8am or after 9pm at the recipient's location."""
    try:
        local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(tz or "America/Chicago"))
    except Exception:
        return False
    return 8 <= local.hour < 21


def outreach_rules_summary() -> dict:
    return {
        "calls_texts": "Only to numbers scrubbed against the National DNC Registry and your internal DNC "
                       "list, between 8am and 9pm recipient-local time. No autodialed/AI-voice calls or "
                       "automated marketing texts to cell phones without prior express written consent.",
        "email": "CAN-SPAM: truthful sender and subject, physical mailing address, working unsubscribe.",
        "disclosure": "State that you are an investor/principal, not a licensed agent, and that you may "
                      "assign the contract.",
        "fair_housing": "Never reference race, religion, national origin, sex, familial status, disability "
                        "or other protected classes in targeting or copy.",
        "states_cleared": sorted(states_cleared()),
        "licensed_in": sorted(states_licensed()),
        "state_rules": {k: v for k, v in STATE_RULES.items() if k in states_cleared()},
    }
