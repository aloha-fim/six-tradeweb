"""Per-price lineage capture and deterministic replay.

For audit/regulatory reproducibility: store the full input set and feature
snapshot behind each consensus price, then re-derive it on demand and confirm the
output is bit-for-bit identical (the engine is a pure function of these inputs).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.analytics.consensus import MODEL_VERSION, consensus_deviation
from app.clients.contributors import ContributorMark


def build_lineage(record, marks, cd, *, liquidity: float, group_prior: float | None) -> dict:
    """Assemble the immutable lineage payload from a priced record."""
    return {
        "cusip": record.cusip,
        "as_of": record.as_of,
        "model_version": MODEL_VERSION,
        "ai_price": float(record.ai_price),
        "consensus": cd.consensus,
        "posterior_price": cd.posterior_price,
        "ci_low": cd.ci_low,
        "ci_high": cd.ci_high,
        "confidence_pct": cd.confidence_pct,
        "features": {
            "ai_price": float(record.ai_price),
            "effective_duration": float(record.effective_duration),
            "confidence": float(getattr(record, "confidence", 0.8) or 0.8),
            "rating_sp": record.rating_sp,
            "sector": getattr(record.sector, "value", str(record.sector)),
            "liquidity": round(liquidity, 4),
        },
        "inputs": {
            "group_prior": group_prior,
            "liquidity": round(liquidity, 4),
            "marks": [{"contributor": m.contributor, "price": m.price,
                       "reliability": m.reliability, "confidence": m.confidence,
                       "age_hours": getattr(m, "age_hours", 0.0)} for m in marks],
            "outliers": cd.outliers,
            "outlier_reasons": cd.outlier_reasons,
        },
    }


def replay(lineage: dict) -> dict:
    """Re-derive the price from stored inputs and compare to what was recorded."""
    f = lineage["features"]
    record = SimpleNamespace(
        cusip=lineage["cusip"], ai_price=f["ai_price"],
        effective_duration=f["effective_duration"], confidence=f["confidence"],
        rating_sp=f["rating_sp"], sector=f["sector"],
        liquidity_score=f["liquidity"] * 100.0,
    )
    marks = [ContributorMark(m["contributor"], m["price"], m["reliability"],
                             m["confidence"], m.get("age_hours", 0.0))
             for m in lineage["inputs"]["marks"]]
    cd = consensus_deviation(record, marks, liquidity=lineage["inputs"]["liquidity"],
                             group_prior=lineage["inputs"]["group_prior"])
    recomputed = {"consensus": cd.consensus, "posterior_price": cd.posterior_price,
                  "ci_low": cd.ci_low, "ci_high": cd.ci_high,
                  "confidence_pct": cd.confidence_pct}
    stored = {k: lineage[k] for k in recomputed}
    reproduced = all(abs(float(recomputed[k]) - float(stored[k])) < 1e-6
                     for k in ("consensus", "posterior_price", "ci_low", "ci_high"))
    reproduced = reproduced and recomputed["confidence_pct"] == stored["confidence_pct"]
    return {"reproduced": reproduced, "model_version": lineage["model_version"],
            "stored": stored, "recomputed": recomputed}
