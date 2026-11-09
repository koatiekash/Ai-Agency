"""The org chart: every business, department and agent lives here.

Edit this file to add agents, change roles, or tune what each one focuses on.
The orchestrator, the Command Center and the 3D city all read from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    role: str                    # one-line job title
    mission: str                 # what the agent is responsible for producing
    tools: tuple[str, ...] = ()  # names of deterministic tools fed into its context


@dataclass(frozen=True)
class Business:
    id: str
    name: str
    goal: str
    research: tuple[AgentSpec, ...]
    executive: AgentSpec
    compliance: AgentSpec
    color: str = "#888888"
    permitted_actions: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# TikTok Shop: home goods
# --------------------------------------------------------------------------- #
TIKTOK = Business(
    id="tiktok",
    name="TikTok Shop: Home Goods",
    color="#ff2d55",
    goal=("Find and launch the next breakout home-goods product on TikTok Shop, "
          "with healthy margin and a creator/affiliate plan, profitable within 30 days."),
    permitted_actions=(
        "shortlist_product", "request_supplier_samples", "draft_listing",
        "recruit_affiliate_creators", "request_marketing_campaign", "kill_product",
    ),
    research=(
        AgentSpec("tt_product", "Product Scout", "Product Research",
                  "Surface candidate home-goods products: problem solved, unit cost, "
                  "landed cost, suggested retail price, supplier options, shipping weight, "
                  "and why it is demonstrable in a 15-second video.",
                  tools=("product_scores",)),
        AgentSpec("tt_trend", "Trend Watcher", "Popularity & Trend Analysis",
                  "Measure popularity momentum: hashtag/view growth, creator adoption, "
                  "week-over-week velocity, seasonality, and saturation risk.",
                  tools=("product_scores",)),
        AgentSpec("tt_sales", "Sales Analyst", "Sales Volume Analysis",
                  "Estimate units/day, GMV, price-band performance, conversion signals and "
                  "competitor shop counts for each candidate.",
                  tools=("product_scores",)),
        AgentSpec("tt_stats", "Market Statistician", "Unit Economics & Market Stats",
                  "Compute contribution margin after TikTok fees, affiliate commission, "
                  "ads and returns; break-even ROAS; review/return-rate risk; and an overall "
                  "go/no-go score for each product.",
                  tools=("product_scores",)),
    ),
    executive=AgentSpec("tt_exec", "TikTok Shop GM", "Business Executive",
                        "Turn research into decisions: which product to push, pricing, "
                        "inventory test size, creator plan and the marketing brief."),
    compliance=AgentSpec("tt_compliance", "TikTok Compliance Analyst", "Compliance",
                         "Check every action against TikTok Shop policies, FTC endorsement "
                         "rules (#ad / paid-partnership disclosure), product-claim "
                         "substantiation, prohibited/restricted categories, and the "
                         "business's margin and budget limits."),
)

# --------------------------------------------------------------------------- #
# Real estate wholesaling
# --------------------------------------------------------------------------- #
WHOLESALE = Business(
    id="wholesale",
    name="Real Estate Wholesale",
    color="#34c759",
    goal=("Lock up properties under contract below market and assign them to cash buyers "
          "for an assignment fee, closing the first deal within 30 days."),
    permitted_actions=(
        "add_lead", "make_offer", "schedule_appointment", "send_outreach",
        "add_cash_buyer", "market_deal_to_buyers", "request_marketing_campaign", "drop_lead",
    ),
    research=(
        AgentSpec("re_hunter", "House Hunter", "House Hunter & Property Analysis",
                  "Find distressed/motivated-seller properties (absentee, tax-delinquent, "
                  "pre-foreclosure, vacant, probate, high equity) and analyze condition, "
                  "comps and estimated ARV.",
                  tools=("property_leads",)),
        AgentSpec("re_deal", "Deal Analyst", "Deal & Offer Analysis",
                  "Underwrite each lead: ARV, repair estimate, Maximum Allowable Offer, "
                  "assignment fee, buyer's profit margin, and an opening/walk-away offer.",
                  tools=("deal_math",)),
        AgentSpec("re_buyers", "Deal & Buyer Finder", "Deal Finder / Buyer Finder",
                  "Build and match the cash-buyer list: active flippers/landlords by zip, "
                  "buy box (price, beds, condition), and which buyer fits which deal.",
                  tools=("buyer_list",)),
        AgentSpec("re_appt", "Appointment Setter", "Appointment Setter",
                  "Draft seller outreach, read seller email replies (context: email.new_replies), "
                  "answer them and book walkthrough appointments, respecting Do-Not-Call status, "
                  "consent, calling hours and anyone who opted out.",
                  tools=("outreach_rules",)),
    ),
    executive=AgentSpec("re_exec", "Wholesale Acquisitions Director", "Business Executive",
                        "Decide which leads to pursue, which offers to send, which "
                        "appointments to book and which buyers to market each contract to."),
    compliance=AgentSpec("re_compliance", "Wholesale Compliance Analyst", "Compliance",
                         "Check TCPA / Do-Not-Call / consent rules, state wholesaling and "
                         "licensing rules, honest disclosure that you're an investor buying "
                         "with the intent to assign, fair-housing language, and that offers "
                         "stay at or under the MAO."),
)

# --------------------------------------------------------------------------- #
# Futures trading
# --------------------------------------------------------------------------- #
TRADING = Business(
    id="trading",
    name="Trading Desk (HeroFX)",
    color="#0a84ff",
    goal=("Trade index, gold and oil CFDs on the HeroFX account with a validated, risk-controlled "
          "edge; grow equity while never breaching the daily-loss and drawdown limits. Position size "
          "is in lots (fractional sizes like 0.10 are allowed)."),
    permitted_actions=(
        "enter_long", "enter_short", "exit_position", "adjust_stop", "stand_aside",
        "promote_strategy", "retire_strategy",
    ),
    research=(
        AgentSpec("tr_chart", "Master Chart Analyzer", "Master Chart Analyzer",
                  "Read market structure across timeframes: trend, key levels, ranges, "
                  "volatility regime, and where the current price sits relative to them.",
                  tools=("market_snapshot",)),
        AgentSpec("tr_signal", "Signal Identifier", "Signal Identifier",
                  "Identify actionable entry/exit signals from the active strategy and "
                  "chart context, with entry, stop and target for each.",
                  tools=("market_snapshot", "strategy_signal")),
        AgentSpec("tr_quant", "Quantitative Analyst", "Quantitative Analyst",
                  "Quantify edge and risk: expectancy, win rate, volatility, position "
                  "size under the risk budget, correlation and regime fit.",
                  tools=("market_snapshot", "backtest_report")),
        AgentSpec("tr_backtest", "Strategy Backtester", "Backtester & Strategy Improver",
                  "Run walk-forward backtests and propose parameter changes. Only "
                  "out-of-sample improvement counts; flag overfitting.",
                  tools=("backtest_report",)),
    ),
    executive=AgentSpec("tr_exec", "Head Trader", "Business Executive",
                        "Decide whether to enter, exit, adjust or stand aside, and whether "
                        "to promote a newly optimized strategy, within the risk limits."),
    compliance=AgentSpec("tr_compliance", "Risk & Compliance Officer", "Compliance",
                         "Enforce hard risk limits (max contracts, daily loss, drawdown, "
                         "mandatory stop), check that the strategy is validated "
                         "out-of-sample, and block anything outside permitted actions."),
)

BUSINESSES: tuple[Business, ...] = (TIKTOK, WHOLESALE, TRADING)

# --------------------------------------------------------------------------- #
# Shared departments
# --------------------------------------------------------------------------- #
MARKETING = (
    AgentSpec("mk_research", "Marketing Researcher", "Audience & Creative Research",
              "Research the audience, hooks and formats that are winning right now for "
              "this business."),
    AgentSpec("mk_creator", "Content Creator", "Ad / Post Creator",
              "Write the ad or post: hook, script, shot list, on-screen text, caption, "
              "hashtags and call to action."),
    AgentSpec("mk_editor", "Editor", "Editor",
              "Tighten the draft: pacing, clarity, first-2-second hook, brand voice and "
              "required disclosures."),
    AgentSpec("mk_perfector", "Creative Director", "Perfector",
              "Score the edited piece against the brief and past winners, and produce the "
              "final version plus 2 A/B variants."),
)

MARKETING_COMPLIANCE = AgentSpec(
    "mk_compliance", "Marketing Compliance Analyst", "Compliance",
    "Check ads/posts for FTC truth-in-advertising and endorsement rules, platform ad "
    "policies, no earnings/profit guarantees, required risk disclaimers, and "
    "fair-housing-safe language.")

CEO = AgentSpec("ceo", "CEO", "Chief Executive",
                "Read every executive's update, set priorities and capital allocation across "
                "the three businesses, and write the daily CEO briefing.")


def all_agents() -> list[dict]:
    """Flat list used by the API / 3D city."""
    out: list[dict] = []
    for b in BUSINESSES:
        for a in b.research:
            out.append({"id": a.id, "name": a.name, "role": a.role, "business": b.id, "dept": "research"})
        out.append({"id": b.executive.id, "name": b.executive.name, "role": b.executive.role,
                    "business": b.id, "dept": "hq"})
        out.append({"id": b.compliance.id, "name": b.compliance.name, "role": b.compliance.role,
                    "business": b.id, "dept": "compliance"})
    for a in MARKETING:
        out.append({"id": a.id, "name": a.name, "role": a.role, "business": "shared", "dept": "marketing"})
    out.append({"id": MARKETING_COMPLIANCE.id, "name": MARKETING_COMPLIANCE.name,
                "role": MARKETING_COMPLIANCE.role, "business": "shared", "dept": "marketing"})
    out.append({"id": CEO.id, "name": CEO.name, "role": CEO.role, "business": "shared", "dept": "command"})
    return out


def paused_businesses(cc=None) -> set[str]:
    """Paused businesses: AGENCY_PAUSED in .env (default: tiktok), overridden from the UI."""
    import os
    default = {x.strip() for x in os.environ.get("AGENCY_PAUSED", "tiktok").split(",") if x.strip()}
    if cc is not None:
        saved = cc.get("paused_businesses")
        if saved is not None:
            return set(saved)
    return default


def business(bid: str) -> Business:
    for b in BUSINESSES:
        if b.id == bid:
            return b
    raise KeyError(bid)
