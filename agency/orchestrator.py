"""One operating cycle of the whole company.

For each business (in parallel):
  1. Deterministic tools gather data (market bars, leads, product metrics, backtests).
  2. Research agents each write a report -> Command Center.
  3. The executive reads the reports and proposes actions (+ an optional marketing brief).
  4. Every action goes through compliance: hard rules, then the compliance agent.
     Rejected -> sent back to the executive with reasons -> revised -> checked again.
  5. Approved actions are executed by the business's executor.
  6. Marketing turns the brief into an ad/post, with its own compliance loop.
  7. The executive files an update to the Command Center.
Then the CEO writes a briefing, and a retrospective turns this cycle's results and
compliance failures into lessons that are fed into every agent's next prompt.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from . import agents, llm, mailer
from .compliance import MAX_ATTEMPTS, hard_rules
from .org import BUSINESSES, CEO, MARKETING, MARKETING_COMPLIANCE, Business, paused_businesses
from .realestate import deals as re_tools
from .store import CommandCenter
from .tiktok import products as tt_tools
from .trading import engine as tr
from .trading.brokers import get_broker
from .trading.risk import RiskLimits

log = logging.getLogger("agency")
OUT = os.path.join(os.path.dirname(__file__), "..", "output")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "item").lower()).strip("-")[:50]


# =========================================================================== #
# Context builders (deterministic tools)
# =========================================================================== #
def trading_context(cc: CommandCenter) -> dict:
    symbol = os.environ.get("TRADING_SYMBOL", "US500")
    df, src = tr.load_bars(symbol)
    champion = tr.Params(**cc.get("trading.champion", asdict(tr.Params())))
    wf = tr.walk_forward(df, symbol, champion)
    sig = int(tr.signals(df, champion).iloc[-1])
    last_atr = float(tr.atr(df).iloc[-1])
    last = float(df["close"].iloc[-1])
    account = cc.get("trading.account", {"equity": 10000.0, "peak_equity": 10000.0, "daily_pnl": 0.0,
                                         "kill_switch": False, "position": None, "day": ""})
    broker = get_broker()
    account = _sync_account(cc, account, broker, symbol) if broker.manages_exits else _mark_position(cc, account, df, symbol)
    return {
        "symbol": symbol, "contract": tr.spec(symbol), "data_source": src,
        "broker": {"name": broker.name, "note": getattr(broker, "reason", "")},
        "market": tr.snapshot(df),
        "strategy": {"champion_params": asdict(champion), "signal_now": sig,
                     "suggested_entry": round(last, 2) if sig else None,
                     "suggested_stop": round(last - sig * champion.stop_atr * last_atr, 2) if sig else None,
                     "suggested_target": round(last + sig * champion.target_atr * last_atr, 2) if sig else None},
        "walk_forward": wf,
        "strategy_oos": {**wf["oos_champion"], "data_source": src},
        "account": account,
        "risk_limits": asdict(RiskLimits()),
    }


def _sync_account(cc: CommandCenter, account: dict, broker, symbol: str) -> dict:
    """Broker-managed account (HeroFX): equity, today's P&L and open position come from the broker."""
    today = dt.date.today().isoformat()
    if account.get("day") != today:
        account["day"] = today
    try:
        st = broker.account() or {}
        account["equity"] = st.get("equity", account["equity"])
        account["daily_pnl"] = st.get("today_net", account.get("daily_pnl", 0.0))
        account["peak_equity"] = max(account.get("peak_equity", 0.0), account["equity"])
        open_pos = broker.positions(symbol)
        if account.get("position") and not open_pos:
            cc.record(business="trading", dept="execution", agent="tr_exec", kind="action",
                      title=f"{symbol} position closed at broker (stop/target)", status="executed",
                      payload={"position": account["position"]})
            account["position"] = None
        elif open_pos and not account.get("position"):
            account["position"] = {"symbol": symbol, "broker_positions": open_pos}
        account["broker"] = broker.name
    except Exception as e:
        log.error("broker sync failed: %s", e)
        account["broker_error"] = str(e)
    cc.set("trading.account", account)
    return account


def _mark_position(cc: CommandCenter, account: dict, df, symbol: str) -> dict:
    """Close an open paper position if the latest bar hit its stop or target; roll the trading day."""
    today = dt.date.today().isoformat()
    if account.get("day") != today:
        account["day"], account["daily_pnl"] = today, 0.0
    pos = account.get("position")
    if pos:
        bar = df.iloc[-1]
        pv = tr.spec(pos["symbol"])["point_value"]
        side = 1 if pos["action"] == "enter_long" else -1
        hit_stop = (bar["low"] <= pos["stop"]) if side > 0 else (bar["high"] >= pos["stop"])
        hit_tgt = pos.get("take_profit") and ((bar["high"] >= pos["take_profit"]) if side > 0 else (bar["low"] <= pos["take_profit"]))
        exit_px = pos["stop"] if hit_stop else (pos["take_profit"] if hit_tgt else None)
        if exit_px is not None:
            pnl = side * (exit_px - pos["entry"]) * pv * pos["contracts"]
            account["equity"] += pnl; account["daily_pnl"] += pnl
            account["peak_equity"] = max(account["peak_equity"], account["equity"])
            account["position"] = None
            cc.record(business="trading", dept="execution", agent="tr_exec", kind="action",
                      title=f"Closed {pos['symbol']} at {exit_px} ({'stop' if hit_stop else 'target'})",
                      status="executed", payload={"pnl": round(pnl, 2), "position": pos})
    cc.set("trading.account", account)
    return account


def wholesale_context(cc: CommandCenter) -> dict:
    leads, lsrc = re_tools.load_leads()
    buyers, bsrc = re_tools.load_buyers()
    underwriting = []
    for l in leads:
        d = re_tools.underwrite(l)
        d["matched_buyers"] = re_tools.match_buyers(l, d, buyers)
        underwriting.append(d)
    # wholesale deals marketed per state in the last 12 months (for unlicensed-deal caps)
    year_ago = dt.datetime.now().timestamp() - 365 * 86400
    deals_12mo: dict[str, int] = {}
    for r in cc.history("wholesale", "action", limit=500):
        act = r["payload"].get("action", {})
        if r["ts"] > year_ago and act.get("action") == "market_deal_to_buyers" and r["status"] != "simulated":
            st = r["payload"].get("lead_state", "")
            deals_12mo[st] = deals_12mo.get(st, 0) + 1
    try:
        replies = mailer.fetch_replies()
    except Exception as e:
        log.error("Gmail fetch failed: %s", e)
        replies = [{"error": str(e)}]
    for r in replies:
        if r.get("from"):
            cc.record(business="wholesale", dept="research", agent="re_appt", kind="report",
                      title=f"Reply from {r['from']}: {r['subject'][:60]}", status="opt-out" if r["opted_out"] else "reply",
                      payload=r)
    return {"leads": leads, "lead_source": lsrc, "buyers": buyers, "buyer_source": bsrc, "deals_12mo": deals_12mo,
            "email": {"configured": mailer.configured(), "sent_today": mailer.sent_today(),
                      "daily_limit": int(os.environ.get("GMAIL_DAILY_LIMIT", 40)), "new_replies": replies,
                      "recent_replies": [x["payload"] for x in cc.history("wholesale", "report", "re_appt", 10)
                                         if x["payload"].get("from")]},
            "underwriting": underwriting, "outreach_rules": re_tools.outreach_rules_summary(),
            "open_appointments": cc.history("wholesale", "action", limit=20)}


def tiktok_context(cc: CommandCenter) -> dict:
    ranked, src = tt_tools.ranked()
    return {"products": ranked, "data_source": src, "min_margin": tt_tools.MIN_MARGIN,
            "fees": {"referral": tt_tools.TIKTOK_REFERRAL_FEE, "affiliate": tt_tools.AFFILIATE_COMMISSION},
            "recent_decisions": [r["title"] for r in cc.history("tiktok", "action", limit=10)]}


CONTEXT = {"trading": trading_context, "wholesale": wholesale_context, "tiktok": tiktok_context}


# =========================================================================== #
# Executors: what "doing" an approved action means for each business
# =========================================================================== #
def execute(cc: CommandCenter, b: Business, a: dict, ctx: dict, simulated: bool) -> tuple[str, dict]:
    if simulated:
        return "simulated", {"note": "Offline mode: decision recorded, nothing executed."}
    os.makedirs(os.path.join(OUT, b.id), exist_ok=True)

    if b.id == "trading":
        account = cc.get("trading.account")
        if a["action"] in ("enter_long", "enter_short"):
            if account.get("position"):
                return "skipped", {"reason": "Already in a position."}
            broker = get_broker()
            fill = broker.place({k: a.get(k) for k in ("action", "symbol", "contracts", "entry", "stop", "take_profit")})
            account["position"] = fill; cc.set("trading.account", account)
            return {"paper": "paper_filled", "herofx-demo": "demo_filled", "herofx-live": "executed"}.get(broker.name, "executed"), fill
        if a["action"] == "exit_position" and account.get("position") and get_broker().manages_exits:
            res = get_broker().close(ctx["symbol"])
            account["position"] = None; cc.set("trading.account", account)
            return "executed", res
        if a["action"] == "exit_position" and account.get("position"):
            pos, last = account["position"], ctx["market"]["last"]
            pv = tr.spec(pos["symbol"])["point_value"]
            pnl = (1 if pos["action"] == "enter_long" else -1) * (last - pos["entry"]) * pv * pos["contracts"]
            account.update(position=None, equity=account["equity"] + pnl, daily_pnl=account["daily_pnl"] + pnl)
            account["peak_equity"] = max(account["peak_equity"], account["equity"])
            cc.set("trading.account", account)
            return "paper_filled", {"exit": last, "pnl": round(pnl, 2)}
        if a["action"] == "promote_strategy":
            cc.set("trading.champion", ctx["walk_forward"]["challenger"])
            return "executed", {"new_champion": ctx["walk_forward"]["challenger"]}
        return "executed", {}

    if b.id == "wholesale":
        if a["action"] == "schedule_appointment":
            path = os.path.join(OUT, "wholesale", "appointments.csv")
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["lead_id", "when", "channel", "notes"])
                w.writerow([a["target"], a.get("scheduled_for"), a.get("channel"), a.get("rationale")])
            return "booked", {"file": path}
        lead = next((l for l in ctx.get("leads", []) if str(l.get("id")) == str(a.get("target"))), {})
        if a.get("message") and (a.get("channel") or "").lower() == "email" and mailer.configured() and lead.get("email"):
            subject = f"Your property at {lead.get('address', '')}"
            return "sent", mailer.send(lead["email"], subject, a["message"])
        if a.get("message"):
            path = os.path.join(OUT, "wholesale", f"{_slug(a['target'])}-{a['action']}.txt")
            with open(path, "w") as f:
                f.write(f"Channel: {a.get('channel')}\nAmount: {a.get('amount')}\n\n{a['message']}\n")
            # Sending needs a provider (Lob for mail, an email API, or a DNC-compliant dialer).
            return "drafted", {"file": path, "send": "connect a sending provider to auto-send"}
        return "recorded", {}

    if b.id == "tiktok":
        if a["action"] == "draft_listing":
            path = os.path.join(OUT, "tiktok", "listings.csv")
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["product", "price", "copy"])
                w.writerow([a["target"], a.get("amount"), a.get("message")])
            return "drafted", {"file": path, "publish": "upload via TikTok Shop Seller Center or Partner API"}
        return "recorded", {}
    return "recorded", {}


# =========================================================================== #
# Compliance loop
# =========================================================================== #
def compliance_loop(cc: CommandCenter, cycle: int, biz_id: str, biz_name: str, goal: str,
                    reviewer, item: dict, ctx: dict, revise, label: str, src: str, owner: str):
    """Returns (approved_item | None, attempts). `revise(item, issues)` returns a new item."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        hard = hard_rules(biz_id if biz_id != "shared" else "marketing", item, ctx)
        review = agents.run(cc, reviewer, biz_id, biz_name, goal,
                            "Review this proposed item for compliance. Approve only if it is permitted, "
                            "honest, and within limits. Hard-rule violations already found are listed; "
                            "if any exist you must not approve.",
                            {"item": item, "hard_rule_violations": hard,
                             "permitted_actions": ctx.get("permitted_actions"),
                             "relevant_context": {k: ctx[k] for k in ("account", "risk_limits", "outreach_rules",
                                                                       "min_margin", "for_business") if k in ctx}},
                            agents.REVIEW_SCHEMA, effort=llm.COMPLIANCE_EFFORT)
        if review.get("simulated"):   # the simulator can't judge; hard rules decide alone
            review["approved"], review["issues"] = True, []
        issues = hard + review.get("issues", []) if (hard or not review.get("approved")) else []
        status = "approved" if not issues else "rejected"
        cc.record(business=biz_id, dept="compliance", agent=reviewer.id, kind="review", cycle=cycle,
                  title=f"{label} (attempt {attempt})", status=status,
                  payload={"item": item, "hard_rule_violations": hard, "review": review})
        if not issues:
            cc.event(src, owner, biz_id, "approved", f"✔ {label}")
            return item, attempt
        cc.event(src, owner, biz_id, "rejected", f"✘ {label}: {issues[0]}")
        for i in hard:
            cc.record(business=biz_id, dept="compliance", agent=reviewer.id, kind="lesson", cycle=cycle,
                      payload={"lesson": f"Compliance rejected '{item.get('action', label)}': {i}"})
        if attempt < MAX_ATTEMPTS:
            item = revise(item, issues + review.get("required_changes", []))
            cc.event(owner, src, biz_id, "handoff", f"↺ revised {label}")
    cc.record(business=biz_id, dept="compliance", agent=reviewer.id, kind="review", cycle=cycle,
              title=f"{label}: dropped after {MAX_ATTEMPTS} attempts", status="escalated", payload={"item": item})
    return None, MAX_ATTEMPTS


# =========================================================================== #
# Business cycle
# =========================================================================== #
def run_business(cc: CommandCenter, b: Business, cycle: int) -> dict:
    R, HQ, CO = f"{b.id}-research", f"{b.id}-hq", f"{b.id}-compliance"
    ctx = CONTEXT[b.id](cc)
    ctx["permitted_actions"] = list(b.permitted_actions)
    if b.id == "trading":
        cc.record(business=b.id, dept="research", agent="tr_backtest", kind="backtest", cycle=cycle,
                  title=f"Walk-forward {ctx['symbol']} ({ctx['data_source']} data)",
                  status="promote" if ctx["walk_forward"]["promote"] else "hold", payload=ctx["walk_forward"])

    # ---- research (parallel) ----
    def research(a):
        cc.event(R, R, b.id, "working", f"{a.name} researching")
        rep = agents.run(cc, a, b.id, b.name, b.goal,
                         "Write your research report for this cycle from the context below.", ctx, agents.REPORT_SCHEMA)
        cc.record(business=b.id, dept="research", agent=a.id, kind="report", cycle=cycle,
                  title=f"{a.name}: {rep.get('summary', '')[:90]}", status="simulated" if rep.get("simulated") else "",
                  payload=rep)
        return a.name, rep
    with ThreadPoolExecutor(max_workers=len(b.research)) as pool:
        reports = dict(pool.map(research, b.research))
    cc.event(R, HQ, b.id, "handoff", f"{len(reports)} research reports → {b.executive.name}")

    # ---- executive decision ----
    exec_ctx = {"research_reports": reports, "data": ctx,
                "recent_history": [{"title": r["title"], "status": r["status"]} for r in cc.history(b.id, limit=25)]}
    decision = agents.run(cc, b.executive, b.id, b.name, b.goal,
                          "Decide this cycle's actions (0-5) using only permitted actions. Fill fields that "
                          "apply and set the rest to null. If marketing support is needed, write a brief. "
                          "Write a concise update to the CEO with KPIs vs targets.",
                          exec_ctx, agents.action_schema(b), effort="high")
    simulated = bool(decision.get("simulated"))
    cc.record(business=b.id, dept="hq", agent=b.executive.id, kind="decision", cycle=cycle,
              title=f"{len(decision.get('actions', []))} actions proposed", status="simulated" if simulated else "",
              payload=decision)

    def revise(item, issues):
        out = agents.run(cc, b.executive, b.id, b.name, b.goal,
                         "Compliance rejected this action. Fix every issue and return exactly one revised "
                         "action in `actions` (or stand down with an empty list).",
                         {"rejected_action": item, "issues": issues, "data": ctx}, agents.action_schema(b))
        return (out.get("actions") or [item])[0]

    results = []
    for a in decision.get("actions", [])[:5]:
        label = f"{a['action']} {a.get('target', '')}".strip()
        cc.event(HQ, CO, b.id, "handoff", f"{label} → compliance")
        approved, attempts = compliance_loop(cc, cycle, b.id, b.name, b.goal, b.compliance, a, ctx,
                                             revise, label, CO, HQ)
        if approved:
            status, detail = execute(cc, b, approved, ctx, simulated)
            lead_state = next((l.get("state") for l in ctx.get("leads", []) if str(l.get("id")) == str(approved.get("target"))), None)
            cc.record(business=b.id, dept="execution", agent=b.executive.id, kind="action", cycle=cycle,
                      title=label, status=status, payload={"action": approved, "result": detail, "attempts": attempts,
                                                           "lead_state": lead_state})
            cc.event(HQ, HQ, b.id, "executed", f"{status}: {label}")
            results.append({"action": label, "status": status})
        else:
            results.append({"action": label, "status": "blocked"})

    if decision.get("marketing_brief"):
        cc.event(HQ, "marketing", b.id, "handoff", f"Campaign brief from {b.name}")
        run_marketing(cc, b, decision["marketing_brief"], ctx, cycle)

    cc.record(business=b.id, dept="hq", agent=b.executive.id, kind="kpi", cycle=cycle,
              title=decision.get("update_to_ceo", "")[:120], status="simulated" if simulated else "",
              payload={"update": decision.get("update_to_ceo"), "kpis": decision.get("kpis"), "results": results})
    cc.event(HQ, "command", b.id, "handoff", f"{b.executive.name} update → Command Center")
    return {"business": b.id, "update": decision.get("update_to_ceo"), "kpis": decision.get("kpis"),
            "results": results}


# =========================================================================== #
# Marketing department
# =========================================================================== #
def run_marketing(cc: CommandCenter, b: Business, brief: str, ctx: dict, cycle: int) -> dict | None:
    research_a, creator, editor, perfector = MARKETING
    mctx = {"brief": brief, "business": b.name, "goal": b.goal,
            "past_assets": [r["payload"].get("hook") for r in cc.history("shared", "asset", limit=10)
                            if r["payload"].get("for") == b.id]}
    notes = agents.run(cc, research_a, "shared", "Marketing Dept", b.goal,
                       "Research the audience, angles and winning formats for this brief.", mctx, agents.REPORT_SCHEMA)
    draft = agents.run(cc, creator, "shared", "Marketing Dept", b.goal,
                       "Create the ad/post from the brief and research.", {**mctx, "research": notes}, agents.ASSET_SCHEMA)
    edited = agents.run(cc, editor, "shared", "Marketing Dept", b.goal,
                        "Edit this draft: sharper hook, tighter pacing, required disclosures.",
                        {**mctx, "draft": draft}, agents.ASSET_SCHEMA)
    final = agents.run(cc, perfector, "shared", "Marketing Dept", b.goal,
                       "Perfect this piece and add 2 A/B variants.", {**mctx, "edited": edited}, agents.ASSET_SCHEMA)

    def as_item(asset):
        return {"action": "publish_asset", "target": asset.get("hook", ""),
                "message": f"{asset.get('hook', '')}\n{asset.get('script', '')}\n{asset.get('caption', '')}",
                "disclosures": asset.get("disclosures", []), "_asset": asset}

    def revise(item, issues):
        fixed = agents.run(cc, perfector, "shared", "Marketing Dept", b.goal,
                           "Compliance rejected this piece. Fix every issue.", {"asset": item["_asset"], "issues": issues},
                           agents.ASSET_SCHEMA)
        return as_item(fixed)

    cc.event("marketing", "marketing", "shared", "working", f"Creative for {b.name}")
    approved, attempts = compliance_loop(cc, cycle, "shared", "Marketing Dept", b.goal, MARKETING_COMPLIANCE,
                                         as_item(final), {**ctx, "for_business": b.id}, revise,
                                         f"{b.id} creative", "marketing", "marketing")
    if not approved:
        return None
    asset = approved["_asset"]
    status = "simulated" if asset.get("simulated") else "ready"
    if status == "ready":
        os.makedirs(os.path.join(OUT, "marketing"), exist_ok=True)
        path = os.path.join(OUT, "marketing", f"{b.id}-c{cycle}-{_slug(asset.get('hook'))}.json")
        with open(path, "w") as f:
            json.dump(asset, f, indent=2)
        asset["file"] = path
    cc.record(business="shared", dept="marketing", agent=perfector.id, kind="asset", cycle=cycle,
              title=f"{b.name}: {asset.get('hook', '')[:80]}", status=status, payload={**asset, "for": b.id})
    cc.event("marketing", f"{b.id}-hq", b.id, "approved", f"Creative ready for {b.name}")
    return asset


# =========================================================================== #
# Whole-company cycle
# =========================================================================== #
def run_cycle(cc: CommandCenter | None = None, only: list[str] | None = None) -> dict:
    cc = cc or CommandCenter()
    cycle = cc.next_cycle()
    cc.set("running", True)
    cc.event("command", "command", "shared", "cycle", f"Cycle {cycle} started"
             + ("" if llm.online() else " (simulated: no Anthropic credentials)"))
    paused = paused_businesses(cc)
    targets = [b for b in BUSINESSES if (not only or b.id in only) and b.id not in paused]
    if paused:
        cc.event("command", "command", "shared", "cycle", f"Paused: {', '.join(sorted(paused))}")
    updates = []
    try:
        with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
            futs = {pool.submit(run_business, cc, b, cycle): b for b in targets}
            for f, b in futs.items():
                try:
                    updates.append(f.result())
                except Exception as e:  # one business failing must not stop the others
                    log.exception("business %s failed", b.id)
                    cc.record(business=b.id, dept="hq", agent=b.executive.id, kind="error", cycle=cycle,
                              title=str(e)[:200], status="error", payload={"error": repr(e)})
                    updates.append({"business": b.id, "error": str(e)})

        briefing = agents.run(cc, CEO, "shared", "the holding company",
                              "Run three automated businesses toward profitability within 30 days, within all rules.",
                              "Write today's CEO briefing from the executive updates and Command Center stats. "
                              "List anything that needs a human (credentials, legal sign-off, spend approval).",
                              {"updates": updates, "paused_businesses": sorted(paused), "stats": cc.stats(),
                               "cycle": cycle}, agents.BRIEFING_SCHEMA,
                              effort="high")
        cc.record(business="shared", dept="command", agent="ceo", kind="briefing", cycle=cycle,
                  title=briefing.get("headline", ""), status="simulated" if briefing.get("simulated") else "",
                  payload=briefing)
        cc.event("command", "command", "shared", "briefing", f"CEO: {briefing.get('headline', '')}")

        for b in targets:
            reviews = [r for r in cc.history(b.id, "review", limit=30) if r["cycle"] == cycle]
            retro = agents.run(cc, b.executive, b.id, b.name, b.goal,
                               "Retrospective: from this cycle's results and compliance reviews, write 1-4 short, "
                               "specific lessons that will improve the next cycle. Tag each with the agent id it "
                               "applies to (or 'retrospective' for everyone).",
                               {"update": next((u for u in updates if u.get("business") == b.id), {}),
                                "reviews": [{"title": r["title"], "status": r["status"],
                                             "issues": r["payload"].get("review", {}).get("issues")} for r in reviews]},
                               agents.LESSONS_SCHEMA)
            if not retro.get("simulated"):
                for l in retro.get("lessons", [])[:4]:
                    cc.record(business=b.id, dept="command", agent=l["agent"] or "retrospective", kind="lesson",
                              cycle=cycle, payload={"lesson": l["lesson"]})
    finally:
        cc.set("running", False)
        cc.set("last_cycle_at", dt.datetime.now().isoformat(timespec="seconds"))
    cc.event("command", "command", "shared", "cycle", f"Cycle {cycle} complete")
    return {"cycle": cycle, "updates": updates}
