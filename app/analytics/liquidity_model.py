"""Composite liquidity score -- a blended metric, not a single number.

Combines trading activity, recency, bid/ask width, and price dispersion into a
0-1 score and a HIGH/MEDIUM/LOW bucket that drives how much the pricing stack
trusts market data versus the curve and the Bayesian prior. The spread and
dispersion terms are scaled to reference levels (real muni spreads are tens of
bp, which would otherwise collapse the raw 1/(1+bp) form to near zero).
"""
from __future__ import annotations

import math

_WEIGHTS = {"trade": 0.35, "recency": 0.25, "spread": 0.25, "dispersion": 0.15}


def composite_liquidity_score(*, trade_count_30d: float, days_since_last_trade: float,
                              avg_spread_bp: float, price_std_bp: float,
                              trade_cap: float = 40.0, spread_ref: float = 50.0,
                              disp_ref: float = 20.0) -> dict:
    trade = min(1.0, math.log1p(max(0.0, trade_count_30d)) / math.log1p(trade_cap))
    recency = 1.0 / (1.0 + max(0.0, days_since_last_trade))
    spread = 1.0 / (1.0 + max(0.0, avg_spread_bp) / spread_ref)
    dispersion = 1.0 / (1.0 + max(0.0, price_std_bp) / disp_ref)
    score = (_WEIGHTS["trade"] * trade + _WEIGHTS["recency"] * recency
             + _WEIGHTS["spread"] * spread + _WEIGHTS["dispersion"] * dispersion)
    score = round(min(1.0, max(0.0, score)), 4)
    return {"liquidity_score": score, "bucket": bucket(score),
            "components": {"trade": round(trade, 3), "recency": round(recency, 3),
                           "spread": round(spread, 3), "dispersion": round(dispersion, 3)}}


def bucket(score: float) -> str:
    return "HIGH" if score > 0.75 else ("MEDIUM" if score >= 0.40 else "LOW")


def score_from_record(record, price_std_bp: float | None = None) -> dict:
    """Derive the composite score from a priced record (bid/ask, trades, recency)."""
    as_of = record.as_of.date() if hasattr(record.as_of, "date") else record.as_of
    last = getattr(record, "last_trade_date", None)
    days = (as_of - last).days if last else 45
    bidask_bp = (float(record.eval_ask) - float(record.eval_bid)) * 100.0
    if price_std_bp is None:                # proxy from the model's own liquidity field
        price_std_bp = max(0.0, (100.0 - float(record.liquidity_score))) * 0.4
    return composite_liquidity_score(
        trade_count_30d=int(getattr(record, "trades_30d", 0) or 0),
        days_since_last_trade=days, avg_spread_bp=bidask_bp, price_std_bp=price_std_bp)
