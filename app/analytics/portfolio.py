"""Portfolio valuation and risk from Ai-Price marks."""
from __future__ import annotations

from .muni import _sector_str, tax_equivalent_yield


def value_portfolio(
    holdings: list[tuple[str, float]],
    price_by_cusip: dict[str, object],
    marginal_rate: float = 0.37,
) -> dict:
    """Mark a portfolio and aggregate risk.

    ``holdings`` is [(cusip, par_amount), ...]; ``price_by_cusip`` maps cusip to
    an Ai-Price record. Market value uses evaluated mid (ai_price) on par/100.
    Duration and yield are market-value weighted; DV01 sums per-position DV01.
    """
    priced: list[dict] = []
    missing: list[str] = []
    total_mv = 0.0
    for cusip, par in holdings:
        rec = price_by_cusip.get(cusip)
        if rec is None:
            missing.append(cusip)
            continue
        mv = float(par) * float(rec.ai_price) / 100.0
        total_mv += mv
        priced.append({"cusip": cusip, "par": float(par), "rec": rec, "mv": mv})

    if total_mv == 0.0:
        return {
            "market_value": 0.0, "par_value": sum(p for _, p in holdings),
            "positions": 0, "missing_cusips": missing,
        }

    w_dur = w_yield = w_tey = w_conf = total_dv01 = 0.0
    sector_w: dict[str, float] = {}
    rating_w: dict[str, float] = {}
    for p in priced:
        rec, mv = p["rec"], p["mv"]
        w = mv / total_mv
        w_dur += w * float(rec.effective_duration)
        w_yield += w * float(rec.ai_yield)
        w_tey += w * tax_equivalent_yield(float(rec.ai_yield), marginal_rate)
        w_conf += w * float(rec.confidence)
        # DV01 per 100 face -> scale to position face value
        total_dv01 += float(rec.dv01) * (p["par"] / 100.0)
        sector_w[_sector_str(rec.sector)] = sector_w.get(_sector_str(rec.sector), 0.0) + w
        rating_w[rec.rating_sp] = rating_w.get(rec.rating_sp, 0.0) + w

    return {
        "par_value": round(sum(float(p) for _, p in holdings), 2),
        "market_value": round(total_mv, 2),
        "positions": len(priced),
        "missing_cusips": missing,
        "weighted_duration": round(w_dur, 3),
        "weighted_yield": round(w_yield, 4),
        "weighted_tax_equivalent_yield": round(w_tey, 4),
        "weighted_confidence": round(w_conf, 3),
        "portfolio_dv01": round(total_dv01, 2),
        "sector_weights": {k: round(v, 4) for k, v in sorted(sector_w.items())},
        "rating_weights": {k: round(v, 4) for k, v in sorted(rating_w.items())},
        "marginal_rate": marginal_rate,
    }
