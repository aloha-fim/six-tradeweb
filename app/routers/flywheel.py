"""The closed-loop flywheel: consume -> validate -> feed back -> retrain.

Clients consume prices (logged), can challenge a valuation, SIX adjudicates,
and accepted corrections become model adjustments that the next Ai-Price
snapshot incorporates -- so feedback measurably improves the next prices.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import TradewebClient
from ..db import get_session
from ..deps import get_tradeweb_client
from ..models import AiPriceQuote, ModelAdjustment, PriceChallenge, UsageEvent
from ..routers.ai_price import _do_refresh, _latest_rows

router = APIRouter(tags=["Flywheel"])


class ChallengeIn(BaseModel):
    cusip: str = Field(min_length=9, max_length=9)
    challenged_price: float = Field(gt=0)
    client: str = Field(default="unspecified", max_length=40)
    note: str = ""


# Synthetic roster of SIX's bank clients (the same banks whose marks form the
# consensus). Used to attribute a challenge and to drive simulated turns.
SIX_CLIENTS = ["Cantonal Bank", "Private Bank", "Asset Manager",
               "Universal Bank", "Insurance GA"]


class ResolveIn(BaseModel):
    action: str = Field(pattern="^(accept|reject)$")
    settled_price: float | None = None


async def _current_version(session: AsyncSession) -> str:
    n = await session.scalar(select(func.count(ModelAdjustment.id))) or 0
    return f"aiprice-2.4-mock+fb{n}" if n else "aiprice-2.4-mock"


@router.post("/challenges", status_code=201)
async def submit_challenge(
    payload: ChallengeIn, session: AsyncSession = Depends(get_session)
) -> dict:
    rows = await _latest_rows(session)
    rec = next((r for r in rows if r.cusip == payload.cusip), None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No current price for {payload.cusip}")
    ch = PriceChallenge(
        cusip=payload.cusip, observed_price=float(rec.ai_price),
        challenged_price=payload.challenged_price, note=payload.note[:240],
        client=(payload.client or "unspecified")[:40], status="pending",
    )
    session.add(ch)
    session.add(UsageEvent(cusip=payload.cusip, kind="challenge"))
    await session.commit()
    await session.refresh(ch)
    return {"id": ch.id, "cusip": ch.cusip, "client": ch.client,
            "observed_price": float(ch.observed_price),
            "challenged_price": float(ch.challenged_price), "status": ch.status}


@router.get("/challenges")
async def list_challenges(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = list((await session.scalars(
        select(PriceChallenge).order_by(PriceChallenge.created_at.desc())
    )).all())
    return [{
        "id": c.id, "cusip": c.cusip, "client": c.client,
        "observed_price": float(c.observed_price),
        "challenged_price": float(c.challenged_price), "status": c.status,
        "settled_price": (float(c.settled_price) if c.settled_price is not None else None),
        "note": c.note,
    } for c in rows]


@router.post("/challenges/{challenge_id}/resolve")
async def resolve_challenge(
    challenge_id: int, payload: ResolveIn, session: AsyncSession = Depends(get_session)
) -> dict:
    ch = await session.get(PriceChallenge, challenge_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if ch.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {ch.status}")
    ch.resolved_at = dt.datetime.now(dt.timezone.utc)
    if payload.action == "reject":
        ch.status = "rejected"
        await session.commit()
        return {"id": ch.id, "status": ch.status}
    settled = payload.settled_price if payload.settled_price is not None else float(ch.challenged_price)
    ch.status = "accepted"
    ch.settled_price = settled
    delta = round(settled - float(ch.observed_price), 4)
    session.add(ModelAdjustment(cusip=ch.cusip, price_delta=delta, challenge_id=ch.id))
    await session.commit()
    return {"id": ch.id, "status": ch.status, "settled_price": settled,
            "price_delta": delta, "note": "fed back to model; next snapshot will reflect it"}


@router.get("/flywheel")
async def flywheel_state(session: AsyncSession = Depends(get_session)) -> dict:
    priced = await session.scalar(select(func.count(func.distinct(AiPriceQuote.cusip)))) or 0
    consumed = await session.scalar(select(func.count(UsageEvent.id))) or 0
    challenged = await session.scalar(select(func.count(PriceChallenge.id))) or 0
    accepted = await session.scalar(
        select(func.count(PriceChallenge.id)).where(PriceChallenge.status == "accepted")
    ) or 0
    adjustments = await session.scalar(select(func.count(ModelAdjustment.id))) or 0
    version = await _current_version(session)
    stages = [
        {"stage": "Tradeweb prices", "metric": priced, "unit": "bonds priced"},
        {"stage": "SIX distributes", "metric": consumed, "unit": "client consumptions"},
        {"stage": "Clients validate", "metric": challenged, "unit": "price challenges"},
        {"stage": "SIX feeds back", "metric": accepted, "unit": "accepted corrections"},
        {"stage": "Tradeweb retrains", "metric": adjustments, "unit": "model adjustments"},
    ]
    return {
        "model_version": version, "loop_turns": adjustments,
        "counters": {"priced": priced, "consumed": consumed, "challenged": challenged,
                     "accepted": accepted, "adjustments": adjustments},
        "stages": stages,
    }


@router.post("/flywheel/simulate")
async def simulate_turn(
    session: AsyncSession = Depends(get_session),
    tradeweb: TradewebClient = Depends(get_tradeweb_client),
) -> dict:
    """Turn the loop once: a client challenges the weakest-confidence price, SIX
    accepts a small correction, and the snapshot is re-ingested with it applied."""
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    target = min(rows, key=lambda r: r.confidence)
    observed = float(target.ai_price)
    prior_turns = await session.scalar(select(func.count(ModelAdjustment.id))) or 0
    client = SIX_CLIENTS[prior_turns % len(SIX_CLIENTS)]
    # client argues the evaluated mid is rich by ~25 cents
    settled = round(observed - 0.25, 4)
    ch = PriceChallenge(cusip=target.cusip, client=client, observed_price=observed,
                        challenged_price=settled, note="auto-simulated turn",
                        status="accepted", settled_price=settled,
                        resolved_at=dt.datetime.now(dt.timezone.utc))
    session.add(ch)
    session.add(UsageEvent(cusip=target.cusip, kind="challenge"))
    await session.flush()
    session.add(ModelAdjustment(cusip=target.cusip, price_delta=round(settled - observed, 4),
                                challenge_id=ch.id))
    await session.commit()

    result = await _do_refresh(session, tradeweb)
    new = next((r for r in await _latest_rows(session) if r.cusip == target.cusip), None)
    turns = await session.scalar(select(func.count(ModelAdjustment.id))) or 0
    return {
        "challenged_cusip": target.cusip, "client": client, "observed_price": observed,
        "settled_price": settled, "price_after_retrain": (float(new.ai_price) if new else None),
        "new_model_version": result.model_version, "loop_turns": turns,
    }
