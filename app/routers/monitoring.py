"""Consolidated monitoring: data, model, and governance health in one view.

Aggregates signals the app already computes (consensus, eval harness, data quality,
audit/lineage counts) into a single health snapshot a desk could poll. No new
analytics — it's the operations lens over the existing ones.
"""
from __future__ import annotations

from statistics import mean, median

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import MODEL_VERSION, consensus_deviation, reliability_weighted, robust_filter
from ..analytics.eval_harness import loop_closure_eval
from ..clients.contributors import contributor_marks
from ..db import get_session
from ..models import (PriceLineage, RecalibrationAudit, SecurityMaster)
from ..reliability import load_reliabilities
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Monitoring"])

_STALE_HOURS = 48.0


def _status(ok: bool) -> str:
    return "ok" if ok else "warn"


@router.get("/monitoring/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    live = await load_reliabilities(session)

    # --- data health -------------------------------------------------------
    n_bonds = len(rows)
    stale_marks = 0
    total_marks = 0
    cds = []
    if rows:
        from statistics import median as _median
        prepped, by_sector_gap = [], {}
        for r in rows:
            marks = contributor_marks(r.cusip, float(r.ai_price), r.sector.value,
                                      float(r.liquidity_score), r.as_of)
            for m in marks:
                m.reliability = live.get(m.contributor, m.reliability)
                total_marks += 1
                if getattr(m, "age_hours", 0.0) > _STALE_HOURS:
                    stale_marks += 1
            kept, _ = robust_filter(marks)
            prepped.append((r, marks))
            by_sector_gap.setdefault(r.sector.value, []).append(
                reliability_weighted(kept) - float(r.ai_price))
        sector_gap = {sec: _median(v) for sec, v in by_sector_gap.items()}
        for r, marks in prepped:
            liq = float(r.liquidity_score) / 100.0
            gp = round(float(r.ai_price) + sector_gap.get(r.sector.value, 0.0), 4)
            cds.append(consensus_deviation(r, marks, liquidity=liq, group_prior=gp))

    stale_pct = round(100 * stale_marks / total_marks, 1) if total_marks else 0.0

    # --- model health ------------------------------------------------------
    if cds:
        dev = [abs(c.deviation_bp) for c in cds]
        conf = [c.confidence_pct for c in cds]
        off_market = sum(1 for c in cds if c.off_market)
        outliers = sum(c.n_outliers for c in cds)
        try:
            ev = loop_closure_eval(rows)
            improvement = ev.get("improvement_pct")
        except Exception:
            improvement = None
        model = {
            "mean_abs_deviation_bp": round(mean(dev), 1),
            "median_confidence_pct": round(median(conf)),
            "off_market": off_market,
            "outliers_removed": outliers,
            "loop_closure_improvement_pct": improvement,
            "status": _status((improvement is None or improvement >= 0) and median(conf) >= 50),
        }
    else:
        model = {"status": "warn", "note": "no priced bonds; refresh Ai-Price"}

    # --- governance / lineage ---------------------------------------------
    n_audit = (await session.scalar(select(func.count()).select_from(RecalibrationAudit))) or 0
    n_lineage = (await session.scalar(select(func.count()).select_from(PriceLineage))) or 0
    n_master = (await session.scalar(select(func.count()).select_from(SecurityMaster))) or 0
    dq = (await session.scalars(select(SecurityMaster.data_quality_score))).all()
    avg_dq = round(float(mean([float(x) for x in dq])), 3) if dq else None

    overall = "ok"
    if not rows or model["status"] == "warn" or stale_pct > 40:
        overall = "warn"

    return {
        "model_version": MODEL_VERSION,
        "overall": overall,
        "data": {
            "bonds_priced": n_bonds,
            "contributor_marks": total_marks,
            "stale_marks": stale_marks,
            "stale_pct": stale_pct,
            "securities_mastered": n_master,
            "avg_data_quality": avg_dq,
            "status": _status(n_bonds > 0 and stale_pct <= 40),
        },
        "model": model,
        "governance": {
            "recalibration_events": n_audit,
            "lineage_records": n_lineage,
            "reliability_spread": {"min": round(min(live.values()), 3),
                                   "max": round(max(live.values()), 3)} if live else None,
            "status": "ok",
        },
    }
