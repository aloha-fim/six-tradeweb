"""Consensus-deviation endpoint: Ai-Price vs the blend of bank-client marks."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from ..analytics import consensus_deviation, recalibrate_reliability
from ..analytics.consensus import MODEL_VERSION, reliability_weighted, robust_filter
from ..clients.contributors import contributor_marks
from ..db import get_session
from ..models import RecalibrationAudit
from ..reliability import load_reliabilities, save_reliabilities
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Consensus"])


@router.get("/consensus")
async def consensus(
    z_flag: float = Query(default=1.5, ge=0.0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from statistics import median

    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    live = await load_reliabilities(session)   # persisted, recalibrated reliabilities

    # pass 1 -- marks + each bond's mark-vs-Ai-Price gap, to learn a sector-level bias
    prepped = []
    by_sector_gap: dict[str, list[float]] = {}
    for r in rows:
        marks = contributor_marks(r.cusip, float(r.ai_price), r.sector.value,
                                  float(r.liquidity_score), r.as_of)
        for m in marks:                         # weight by live (persisted) reliability
            m.reliability = live.get(m.contributor, m.reliability)
        kept, _ = robust_filter(marks)
        quick = reliability_weighted(kept)
        prepped.append((r, marks))
        by_sector_gap.setdefault(r.sector.value, []).append(quick - float(r.ai_price))
    sector_gap = {sec: median(v) for sec, v in by_sector_gap.items()}

    # pass 2 -- full deviation, liquidity-aware, anchored to the sector gap when sparse.
    # The group prior is this bond's Ai-Price shifted by the sector's typical gap, so a
    # thinly-covered bond borrows the sector pattern at its own price level (not an
    # absolute cross-bond price).
    out = []
    for r, marks in prepped:
        liq = float(r.liquidity_score) / 100.0
        gp = round(float(r.ai_price) + sector_gap.get(r.sector.value, 0.0), 4)
        cd = consensus_deviation(r, marks, z_flag, liquidity=liq, group_prior=gp)
        rec = asdict(cd)
        rec["description"] = r.description
        rec["ai_price"] = float(r.ai_price)
        rec["liquidity"] = round(liq, 3)
        rec["marks"] = [m.price for m in marks]
        out.append(rec)
    out.sort(key=lambda x: abs(x["z"]), reverse=True)
    total_out = sum(x["n_outliers"] for x in out)
    off = [x for x in out if x["off_market"]]
    return {
        "source": "synthetic contributor marks (production requires multi-source ingest)",
        "model_version": MODEL_VERSION,
        "method": ("MAD outlier filter + reliability-weighted robust consensus; "
                   "liquidity-aware Bayesian posterior (Ai-Price prior + marks, hierarchical "
                   "sector anchor for sparse bonds, executed-trade ground truth) with 95% CI; "
                   "reliability recalibrated vs executed trades"),
        "z_flag": z_flag, "screened": len(out), "off_market": len(off),
        "outliers_removed": total_out,
        "records": out,
    }


class RecalibrateIn(BaseModel):
    cusip: str
    executed_trade: float


@router.post("/consensus/recalibrate")
async def recalibrate(payload: RecalibrateIn, session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    row = next((r for r in rows if r.cusip == payload.cusip), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown CUSIP in latest snapshot")
    live = await load_reliabilities(session)
    marks = contributor_marks(row.cusip, float(row.ai_price), row.sector.value,
                              float(row.liquidity_score), row.as_of)
    for m in marks:
        m.reliability = live.get(m.contributor, m.reliability)
    updates = recalibrate_reliability(marks, payload.executed_trade)
    # persist new reliabilities (feeds forward) and record the audit trail
    await save_reliabilities(session, {u["contributor"]: u["new_reliability"] for u in updates})
    session.add(RecalibrationAudit(cusip=payload.cusip, executed_trade=payload.executed_trade,
                                   model_version=MODEL_VERSION, detail=updates))
    await session.commit()
    return {"cusip": payload.cusip, "executed_trade": payload.executed_trade,
            "model_version": MODEL_VERSION,
            "method": "reliability EWMA toward closeness to the executed trade (ground truth); persisted",
            "contributors": updates}


@router.get("/consensus/audit")
async def consensus_audit(session: AsyncSession = Depends(get_session)) -> dict:
    from sqlalchemy import select
    rows = (await session.scalars(
        select(RecalibrationAudit).order_by(RecalibrationAudit.id.desc()).limit(50)
    )).all()
    return {"model_version": MODEL_VERSION, "events": [
        {"id": a.id, "ts": a.ts.isoformat(), "cusip": a.cusip,
         "executed_trade": float(a.executed_trade), "model_version": a.model_version,
         "detail": a.detail} for a in rows]}


@router.post("/consensus/snapshot")
async def snapshot(session: AsyncSession = Depends(get_session)) -> dict:
    """Persist an immutable, replayable lineage record for every priced bond."""
    from statistics import median

    from ..lineage import build_lineage
    from ..models import PriceLineage

    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    live = await load_reliabilities(session)
    prepped, by_sector_gap = [], {}
    for r in rows:
        marks = contributor_marks(r.cusip, float(r.ai_price), r.sector.value,
                                  float(r.liquidity_score), r.as_of)
        for m in marks:
            m.reliability = live.get(m.contributor, m.reliability)
        kept, _ = robust_filter(marks)
        prepped.append((r, marks))
        by_sector_gap.setdefault(r.sector.value, []).append(
            reliability_weighted(kept) - float(r.ai_price))
    sector_gap = {sec: median(v) for sec, v in by_sector_gap.items()}

    ids = []
    for r, marks in prepped:
        liq = float(r.liquidity_score) / 100.0
        gp = round(float(r.ai_price) + sector_gap.get(r.sector.value, 0.0), 4)
        cd = consensus_deviation(r, marks, liquidity=liq, group_prior=gp)
        lin = build_lineage(r, marks, cd, liquidity=liq, group_prior=gp)
        row = PriceLineage(cusip=lin["cusip"], as_of=lin["as_of"],
                           model_version=lin["model_version"], ai_price=lin["ai_price"],
                           consensus=lin["consensus"], posterior_price=lin["posterior_price"],
                           ci_low=lin["ci_low"], ci_high=lin["ci_high"],
                           confidence_pct=lin["confidence_pct"], features=lin["features"],
                           inputs=lin["inputs"])
        session.add(row)
        await session.flush()
        ids.append(row.id)
    await session.commit()
    return {"model_version": MODEL_VERSION, "snapshotted": len(ids), "lineage_ids": ids}


@router.get("/consensus/lineage/{lineage_id}")
async def get_lineage(lineage_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from ..models import PriceLineage
    a = await session.get(PriceLineage, lineage_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Unknown lineage id")
    return {"id": a.id, "ts": a.ts.isoformat(), "cusip": a.cusip, "as_of": a.as_of.isoformat(),
            "model_version": a.model_version, "ai_price": float(a.ai_price),
            "consensus": float(a.consensus), "posterior_price": float(a.posterior_price),
            "ci_low": float(a.ci_low), "ci_high": float(a.ci_high),
            "confidence_pct": a.confidence_pct, "features": a.features, "inputs": a.inputs}


@router.post("/consensus/lineage/{lineage_id}/replay")
async def replay_lineage(lineage_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from ..lineage import replay
    from ..models import PriceLineage
    a = await session.get(PriceLineage, lineage_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Unknown lineage id")
    lineage = {"cusip": a.cusip, "model_version": a.model_version, "consensus": float(a.consensus),
               "posterior_price": float(a.posterior_price), "ci_low": float(a.ci_low),
               "ci_high": float(a.ci_high), "confidence_pct": a.confidence_pct,
               "features": a.features, "inputs": a.inputs}
    return {"lineage_id": lineage_id, **replay(lineage)}
