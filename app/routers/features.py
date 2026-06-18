"""Offline feature-store endpoints: materialize, list, and time-travel."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..features import FEATURE_SET_VERSION, build_features
from ..models import BondFeatureSnapshot
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Feature store"])


def _serialize(f: BondFeatureSnapshot) -> dict:
    return {"cusip": f.cusip, "as_of": f.as_of.isoformat(),
            "feature_set_version": f.feature_set_version, "sector": f.sector,
            "rating_score": float(f.rating_score), "duration": f.duration,
            "convexity": f.convexity, "liquidity_score": f.liquidity_score,
            "benchmark_spread_bp": f.benchmark_spread_bp, "trade_count_30d": f.trade_count_30d,
            "volatility_30d": f.volatility_30d, "ai_price": float(f.ai_price)}


async def upsert_snapshot(session: AsyncSession, feats: dict) -> bool:
    """Insert one immutable snapshot; skip if (cusip, as_of, version) already exists."""
    exists = await session.scalar(select(BondFeatureSnapshot.id).where(
        BondFeatureSnapshot.cusip == feats["cusip"],
        BondFeatureSnapshot.as_of == feats["as_of"],
        BondFeatureSnapshot.feature_set_version == feats["feature_set_version"]))
    if exists is not None:
        return False
    session.add(BondFeatureSnapshot(
        cusip=feats["cusip"], as_of=feats["as_of"],
        feature_set_version=feats["feature_set_version"], sector=feats["sector"],
        rating_score=feats["rating_score"], duration=feats["duration"],
        convexity=feats["convexity"], liquidity_score=feats["liquidity_score"],
        benchmark_spread_bp=feats["benchmark_spread_bp"],
        trade_count_30d=feats["trade_count_30d"], volatility_30d=feats["volatility_30d"],
        ai_price=feats["ai_price"]))
    return True


@router.post("/features/materialize")
async def materialize(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    n = 0
    for r in rows:
        if await upsert_snapshot(session, build_features(r)):
            n += 1
    await session.commit()
    return {"feature_set_version": FEATURE_SET_VERSION, "bonds": len(rows), "materialized": n}


@router.get("/features")
async def list_features(cusip: str | None = Query(default=None),
                        session: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(BondFeatureSnapshot).order_by(BondFeatureSnapshot.as_of.desc()).limit(200)
    if cusip:
        stmt = select(BondFeatureSnapshot).where(BondFeatureSnapshot.cusip == cusip).order_by(
            BondFeatureSnapshot.as_of.desc()).limit(200)
    rows = (await session.scalars(stmt)).all()
    return {"feature_set_version": FEATURE_SET_VERSION, "count": len(rows),
            "features": [_serialize(f) for f in rows]}


@router.get("/features/{cusip}")
async def feature_as_of(cusip: str, as_of: dt.datetime | None = Query(default=None),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Time-travel: the latest snapshot at or before `as_of` (or the most recent)."""
    stmt = select(BondFeatureSnapshot).where(BondFeatureSnapshot.cusip == cusip)
    if as_of is not None:
        stmt = stmt.where(BondFeatureSnapshot.as_of <= as_of)
    stmt = stmt.order_by(BondFeatureSnapshot.as_of.desc()).limit(1)
    row = await session.scalar(stmt)
    if row is None:
        raise HTTPException(status_code=404, detail="No feature snapshot at or before that time")
    return {"point_in_time": True, "queried_as_of": as_of.isoformat() if as_of else None,
            **_serialize(row)}
