"""Evidence endpoints: loop-closure eval, consensus lead-time, reach sizing."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics.eval_harness import loop_closure_eval
from ..analytics.lead_time import estimate_lead, lead_series
from ..analytics.reach import reach_sizing
from ..db import get_session
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Evals"])


@router.get("/eval/loop-closure")
async def eval_loop_closure(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    return loop_closure_eval(rows)


@router.get("/eval/lead-time")
async def eval_lead_time(lead: int = 2, session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    out = []
    for r in sorted(rows, key=lambda x: x.liquidity_score)[:4]:   # the illiquid, interesting ones
        cons, ai = lead_series(r.cusip, float(r.ai_price), days=14, lead=lead)
        lag, corr = estimate_lead(cons, ai)
        out.append({"cusip": r.cusip, "description": r.description,
                    "consensus": cons, "ai_price": ai, "lead_days": lag, "corr": corr})
    return {"illustrative": True,
            "method": "lag of peak cross-correlation between the consensus path and the eval path",
            "days": 14, "series": out}


@router.get("/eval/reach")
async def eval_reach() -> dict:
    return reach_sizing()
