# Agency City: an automated AI company

Three businesses — **Real Estate Wholesale**, **Trading (HeroFX)** and **TikTok Shop** (paused) — each with
a research department, an executive "building" that takes action, and its own compliance analyst. A shared
**Marketing Studio** makes the ads, and a **Command Center** holds every report, test, decision and CEO briefing.
It all runs on a timer, and you can watch it work in a neon isometric city.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY at minimum
python server.py              # open http://localhost:8000
```

Without an API key the agency runs in **simulated mode**: the whole pipeline and the city work, but agent
output is placeholder text and nothing is executed. The UI shows `SIMULATED` while this is the case.

Other ways to run it:

```bash
python run.py                 # one cycle, all active businesses, prints the results
python run.py trading         # one business
python run.py --loop 60       # headless, a cycle every 60 minutes
python -m pytest -q tests     # tests (offline, no API calls)
```

## Run it in the cloud (open it from your phone or any computer)

The repository includes a `Dockerfile` and a Render blueprint (`render.yaml`). Render runs the agency
24/7 on an always-on server with a permanent disk for the Command Center, behind HTTPS and a password.
Cost: about $7.25/month (Starter instance + 1 GB disk), plus your Claude API usage.

1. Sign up at render.com and connect your GitHub account (allow access to this repository).
2. **New → Blueprint**, choose this repository and branch, then **Apply**.
3. Fill in the values it asks for. Required: `AGENCY_PASSWORD` (10+ characters, this is your login) and
   `ANTHROPIC_API_KEY`. Leave HeroFX, ATTOM and Gmail blank until you have them. Add them later under the
   service's **Environment** tab, and the service restarts with them.
4. When the deploy finishes, open the `https://koatie-ai-agency.onrender.com` address it shows (the exact
   name may differ), sign in, and on your phone use **Add to Home Screen** to get an app icon.

From then on, every push to the branch redeploys automatically. Upload lead lists, buyer lists or price
history from the **Upload data** box in the Command Center, from any device.

Any other host that runs Docker works the same way: set `HOST=0.0.0.0`, `AGENCY_HOME` to a persistent
volume, and `AGENCY_PASSWORD`. The server refuses to start in hosted mode without a password.

## The org chart (`agency/org.py`)

| Building | Agents |
|---|---|
| TikTok Research | Product Scout · Trend Watcher (popularity) · Sales Analyst (volume) · Market Statistician (unit economics) |
| TikTok Shop | TikTok Shop GM |
| Wholesale Research | House Hunter · Deal Analyst · Deal & Buyer Finder · Appointment Setter |
| Wholesale Office | Acquisitions Director |
| Trading Research | Master Chart Analyzer · Signal Identifier · Quantitative Analyst · Strategy Backtester |
| Futures Desk | Head Trader |
| Compliance (×3) | TikTok Compliance · Wholesale Compliance · Risk & Compliance Officer |
| Marketing Studio | Researcher → Creator → Editor → Creative Director, then Marketing Compliance |
| Command Center | CEO; the permanent archive (`data/command_center.db`) |

## What one cycle does (`agency/orchestrator.py`)

1. **Tools gather data** with plain code: futures bars plus a walk-forward backtest, property leads with
   ARV/repairs/MAO and buyer matches, TikTok product scores (margin, growth, saturation, break-even ROAS).
2. **Research agents** each write a structured report. All reports are saved to the Command Center.
3. **The executive** reads them and proposes up to 5 actions from that business's permitted list, plus an
   optional marketing brief and an update for the CEO.
4. **Compliance** checks every action: hard rules in code first (`agency/compliance.py`, `trading/risk.py`),
   then the compliance agent. **Rejected items go back to the executive with the reasons, get revised, and are
   checked again** (up to 3 rounds). Anything still failing is dropped and logged as escalated.
5. **Approved actions run**: paper or live orders, outreach drafts, appointment bookings, listing drafts.
6. **Marketing** turns the brief into a finished ad or post (hook, script, shot list, captions, A/B variants),
   with its own compliance loop. Finished assets go to `output/marketing/`.
7. **The CEO** writes a briefing, including anything that needs a person.
8. **Self-improvement**: a retrospective writes lessons from results and rejections. Each agent gets the most
   recent lessons in its next prompt. The trading strategy re-optimizes walk-forward every cycle and is
   promoted only when it beats the current strategy **on data it was not tuned on**.

## Connecting real data and actions

| To turn on | Do this |
|---|---|
| Agent reasoning | `ANTHROPIC_API_KEY` |
| HeroFX (demo first) | Put your TradeLocker login in `.env` (`HEROFX_EMAIL`, `HEROFX_PASSWORD`, `HEROFX_SERVER`, demo URL). The desk then uses HeroFX's real price history and trades the demo account. Set `TRADING_SYMBOL` to HeroFX's exact symbol name. Until it's connected, the desk paper-trades on synthetic data, and **compliance blocks every entry while the strategy is validated on synthetic data.** |
| Live trading | Switch `HEROFX_TL_ENV` to `https://live.tradelocker.com` **and** set `LIVE_TRADING=true`. Both are required. Hard risk limits, broker-side stops and the kill switch apply in every mode. |
| Property data | `ATTOM_API_KEY` + `WHOLESALE_ZIPS`, or drop a PropStream/BatchLeads export at `data/leads.csv`. Add `dnc`, `consent` and `timezone` columns before any call or text can pass compliance. |
| Cash buyers | `data/buyers.csv`: name, zips, max_price, min_beds, strategy, proof_of_funds, email |
| Wholesale states | Footprint: the 12 Midwest states + Florida (`WHOLESALE_STATES_CLEARED`). Per-state rules live in `agency/realestate/deals.py`: IL, NE and WI need a license to market contracts publicly (IL and WI also cap unlicensed deals at 1 per 12 months), and several states require an "equitable interest" disclosure. Add states where you or a partner broker hold a license to `WHOLESALE_LICENSED_STATES`. Have an attorney confirm the rules. |
| Gmail | `GMAIL_ADDRESS` + a Gmail **app password** + `BUSINESS_NAME`/`BUSINESS_ADDRESS`. Approved offer emails are sent (40/day cap by default). Seller replies are read each cycle for the Appointment Setter. Anyone who replies "unsubscribe"/"stop" is never emailed again. |
| Pausing a business | `AGENCY_PAUSED=tiktok` (default), or the Pause / Resume button on any business building in the city. |
| TikTok products (paused) | Export from Kalodata / FastMoss / Shoplus to `data/tiktok_products.csv` (columns listed in `agency/tiktok/products.py`). |
| Auto-sending outreach | Wholesale outreach is drafted to `output/wholesale/`. Connect a sender (Lob for mail, an email API, or a DNC-compliant dialer) to send it automatically. |
| Publishing listings and posts | Listings go to `output/tiktok/listings.csv`. Uploading through TikTok Shop's Partner API needs an approved app. |

## Honest expectations

- **No system can promise profit in 30 days**, and futures trading can lose more than a small account holds.
  The trading desk is built to fail safe: in-code risk limits, mandatory stops, a daily-loss stop, a
  drawdown stop, a kill switch, and no promotion without out-of-sample proof. Paper-trade on real data
  until the walk-forward results are convincing.
- Wholesaling is regulated differently in each state, and calls and texts fall under the TCPA (fines run
  $500 to $1,500 per message). The compliance rules here are guardrails, not legal advice. Have an attorney
  sign off before you turn on outreach.
- Cost: each cycle makes roughly 40–60 Claude calls. Lower `AGENCY_EFFORT`, run cycles less often, or use a
  smaller model (`AGENCY_MODEL`) to spend less.

## Layout

```
agency/org.py            org chart: businesses, agents, permitted actions
agency/orchestrator.py   the cycle, executors, marketing pipeline, compliance loop
agency/agents.py         prompts, JSON schemas, lessons fed into prompts
agency/compliance.py     hard rules for every business
agency/llm.py            Claude calls (structured output, refusal fallback, offline simulator)
agency/store.py          Command Center database
agency/trading/          data, strategy, backtester, walk-forward optimizer, risk limits, brokers (HeroFX/TradeLocker)
agency/mailer.py         Gmail send + reply reading, opt-out list, daily cap
agency/realestate/       leads (ATTOM/CSV), deal math, buyer matching, outreach rules
agency/tiktok/           product scoring
server.py                API + scheduler + city UI      run.py   headless runner
web/index.html           the city
```
