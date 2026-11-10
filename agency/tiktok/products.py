"""TikTok Shop product scoring.

TikTok doesn't offer a public product-analytics API, so research data comes from CSV
exports of tools like Kalodata, FastMoss or Shoplus, saved to data/tiktok_products.csv.
Columns used (case-insensitive; missing ones are fine):
  product, category, price, unit_cost, shipping_cost, units_7d, units_prev_7d,
  gmv_7d, views_7d, views_prev_7d, creators, shops, rating, return_rate, source
"""
from __future__ import annotations

import csv
import math
import os

from ..paths import DATA_DIR

DATA = os.path.join(DATA_DIR, "tiktok_products.csv")

TIKTOK_REFERRAL_FEE = float(os.environ.get("TIKTOK_FEE", 0.08))       # check your current category rate
AFFILIATE_COMMISSION = float(os.environ.get("TIKTOK_AFFILIATE", 0.15))
MIN_MARGIN = float(os.environ.get("TIKTOK_MIN_MARGIN", 0.30))

SAMPLE = [
    {"product": "Magnetic under-cabinet spice rack", "category": "kitchen", "price": 24.99, "unit_cost": 5.10,
     "shipping_cost": 3.2, "units_7d": 2100, "units_prev_7d": 1200, "views_7d": 4.1e6, "views_prev_7d": 2.0e6,
     "creators": 85, "shops": 14, "rating": 4.6, "return_rate": 0.04},
    {"product": "Collapsible over-sink drying rack", "category": "kitchen", "price": 19.99, "unit_cost": 4.40,
     "shipping_cost": 2.8, "units_7d": 3300, "units_prev_7d": 3100, "views_7d": 5.0e6, "views_prev_7d": 4.8e6,
     "creators": 410, "shops": 96, "rating": 4.4, "return_rate": 0.06},
    {"product": "Motion-sensor closet light bar", "category": "lighting", "price": 15.99, "unit_cost": 6.90,
     "shipping_cost": 2.5, "units_7d": 900, "units_prev_7d": 500, "views_7d": 1.6e6, "views_prev_7d": 0.7e6,
     "creators": 40, "shops": 9, "rating": 4.2, "return_rate": 0.09},
]


def load() -> tuple[list[dict], str]:
    if os.path.exists(DATA):
        with open(DATA, newline="", encoding="utf-8-sig") as f:
            rows = [{k.strip().lower(): v for k, v in r.items()} for r in csv.DictReader(f)]
        if rows:
            return rows, "csv"
    return SAMPLE, "sample"


def _f(r: dict, k: str, d: float = 0.0) -> float:
    try:
        return float(r.get(k) or d)
    except (TypeError, ValueError):
        return d


def score(r: dict) -> dict:
    price, cost, ship = _f(r, "price"), _f(r, "unit_cost"), _f(r, "shipping_cost")
    fees = price * (TIKTOK_REFERRAL_FEE + AFFILIATE_COMMISSION)
    returns = price * _f(r, "return_rate", 0.05)
    contribution = price - cost - ship - fees - returns
    margin = contribution / price if price else 0
    unit_growth = (_f(r, "units_7d") + 1) / (_f(r, "units_prev_7d") + 1) - 1
    view_growth = (_f(r, "views_7d") + 1) / (_f(r, "views_prev_7d") + 1) - 1
    saturation = _f(r, "shops") / max(_f(r, "creators"), 1)            # many shops per creator = crowded
    competition = math.log10(_f(r, "shops") + 1)
    break_even_roas = price / contribution if contribution > 0 else None
    total = (40 * max(min(margin, 0.6), -0.2) / 0.6 + 25 * max(min(unit_growth, 1.5), -0.5) / 1.5
             + 15 * max(min(view_growth, 1.5), -0.5) / 1.5 + 10 * (_f(r, "rating", 4.0) - 3.5) / 1.5
             - 10 * min(competition / 2.5, 1) - 5 * min(saturation, 1))
    return {
        "product": r.get("product"), "price": price, "contribution_per_unit": round(contribution, 2),
        "margin": round(margin, 3), "units_7d": _f(r, "units_7d"), "unit_growth_wow": round(unit_growth, 2),
        "view_growth_wow": round(view_growth, 2), "shops": _f(r, "shops"), "creators": _f(r, "creators"),
        "est_gmv_7d": round(_f(r, "gmv_7d") or price * _f(r, "units_7d")),
        "break_even_roas": round(break_even_roas, 2) if break_even_roas else None,
        "passes_margin_floor": margin >= MIN_MARGIN, "score": round(total, 1),
    }


def ranked() -> tuple[list[dict], str]:
    rows, src = load()
    return sorted((score(r) for r in rows), key=lambda x: x["score"], reverse=True), src
