"""Backtesting pipeline.

For each bond it builds a deterministic point-in-time history, asks "what would the
consensus engine have predicted on each past date" using only information available
then, and compares to the trade that printed afterward. It also scores the raw
Ai-Price as a baseline, so the backtest shows whether the consensus loop actually
improves accuracy against realized trades.

Leakage prevention is structural: the prediction at date d uses only the Ai-Price
and contributor marks as of d; the realized trade is never an input to features or
marks. The data is synthetic (real history would plug in unchanged), but the
pipeline -- point-in-time features, no leakage, predict-vs-realized, versioned
metrics -- is production-shaped.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import uuid
from statistics import mean
from types import SimpleNamespace

from .analytics.consensus import MODEL_VERSION, consensus_deviation
from .clients.contributors import contributor_marks, systematic_bias
from .features import build_features


def _rng(key: str) -> random.Random:
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))


def synthetic_history(record, days: int) -> list[tuple[dt.datetime, float, float]]:
    """Return [(as_of, ai_price_d, actual_trade_d)] for `days` past business days.

    The market trades a touch below the (systematically high) Ai-Price; contributor
    marks observe that truer level, so a working consensus should beat raw Ai-Price.
    """
    rng = _rng(f"{record.cusip}|backtest")
    sector = getattr(record.sector, "value", str(record.sector))
    bias = systematic_bias(sector)
    illiq = max(0.0, (100.0 - float(record.liquidity_score)) / 100.0)
    base_date = record.as_of.date() if hasattr(record.as_of, "date") else record.as_of
    price = float(record.ai_price)
    out = []
    for k in range(days, 0, -1):
        price = price + rng.gauss(0, 0.05 + illiq * 0.10)
        ai_d = round(price, 4)
        exec_noise = rng.gauss(0, 0.02 + illiq * 0.05)
        actual_d = round(ai_d - bias + exec_noise, 4)          # realized trade level
        as_of = dt.datetime.combine(base_date - dt.timedelta(days=k),
                                    dt.time(21, 0), tzinfo=dt.timezone.utc)
        out.append((as_of, ai_d, actual_d))
    return out


def _stub(record, as_of: dt.datetime, ai_d: float):
    return SimpleNamespace(
        cusip=record.cusip, as_of=as_of, ai_price=ai_d,
        effective_duration=float(record.effective_duration), convexity=float(record.convexity),
        confidence=float(record.confidence), rating_sp=record.rating_sp,
        sector=getattr(record.sector, "value", str(record.sector)),
        liquidity_score=float(record.liquidity_score),
        benchmark_spread_bp=float(record.benchmark_spread_bp),
        trades_30d=int(getattr(record, "trades_30d", 0) or 0),
    )


def run_backtest(records, days: int = 10) -> dict:
    run_id = str(uuid.uuid4())
    rows = []
    for r in records:
        liq = float(r.liquidity_score) / 100.0
        for as_of, ai_d, actual_d in synthetic_history(r, days):
            stub = _stub(r, as_of, ai_d)
            feats = build_features(stub)                       # parity + leakage-safe
            marks = contributor_marks(r.cusip, ai_d, stub.sector, float(r.liquidity_score), as_of)
            cd = consensus_deviation(stub, marks, liquidity=liq, group_prior=None)
            predicted = cd.posterior_price
            error_bp = round((predicted - actual_d) * 100, 2)        # price basis points
            base_err_bp = round((ai_d - actual_d) * 100, 2)
            rows.append({
                "cusip": r.cusip, "as_of": as_of, "sector": stub.sector,
                "predicted": predicted, "baseline_ai": ai_d, "actual": actual_d,
                "error_bp": error_bp, "abs_error_bp": abs(error_bp),
                "baseline_abs_error_bp": abs(base_err_bp), "features": feats,
            })

    summary = _summarize(rows)
    return {"run_id": run_id, "model_version": MODEL_VERSION, "days": days,
            "summary": summary, "rows": rows}


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    err = [r["error_bp"] for r in rows]
    abs_err = [r["abs_error_bp"] for r in rows]
    base_abs = [r["baseline_abs_error_bp"] for r in rows]
    mae = mean(abs_err)
    base_mae = mean(base_abs)
    by_sector: dict[str, list[float]] = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r["abs_error_bp"])
    return {
        "n": len(rows),
        "mae_bp": round(mae, 2),
        "rmse_bp": round(math.sqrt(mean([e * e for e in err])), 2),
        "bias_bp": round(mean(err), 2),
        "baseline_mae_bp": round(base_mae, 2),
        "improvement_pct": round(100 * (base_mae - mae) / base_mae, 1) if base_mae else 0.0,
        "by_sector_mae_bp": {s: round(mean(v), 2) for s, v in by_sector.items()},
    }
