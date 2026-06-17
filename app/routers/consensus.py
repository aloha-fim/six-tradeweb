"""Consensus-deviation endpoint: Ai-Price vs the blend of bank-client marks."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import consensus_deviation
from ..clients.contributors import contributor_marks
from ..db import get_session
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Consensus"])


@router.get("/consensus")
async def consensus(
    z_flag: float = Query(default=1.5, ge=0.0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    out = []
    for r in rows:
        marks = contributor_marks(r.cusip, float(r.ai_price), r.sector.value, float(r.liquidity_score), r.as_of)
        cd = consensus_deviation(r, marks, z_flag)
        rec = asdict(cd)
        rec["description"] = r.description
        rec["ai_price"] = float(r.ai_price)
        rec["marks"] = [m.price for m in marks]
        out.append(rec)
    out.sort(key=lambda x: abs(x["z"]), reverse=True)
    off = [x for x in out if x["off_market"]]
    return {
        "source": "synthetic contributor marks (production requires multi-source ingest)",
        "method": "Ai-Price vs median of bank-client marks; z = deviation / bank dispersion",
        "z_flag": z_flag, "screened": len(out), "off_market": len(off),
        "records": out,
    }
