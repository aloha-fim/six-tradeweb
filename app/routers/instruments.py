"""SIX Swiss Exchange listed instruments — SIX's own core business."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AssetClass, Instrument
from ..schemas import InstrumentIn, InstrumentOut

router = APIRouter(prefix="/instruments", tags=["SIX instruments"])


@router.get("", response_model=list[InstrumentOut])
async def list_instruments(
    asset_class: AssetClass | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.symbol)
    if asset_class is not None:
        stmt = stmt.where(Instrument.asset_class == asset_class)
    return list((await session.scalars(stmt)).all())


@router.get("/{isin}", response_model=InstrumentOut)
async def get_instrument(
    isin: str, session: AsyncSession = Depends(get_session)
) -> Instrument:
    inst = await session.scalar(select(Instrument).where(Instrument.isin == isin))
    if inst is None:
        raise HTTPException(status_code=404, detail=f"No instrument with ISIN {isin}")
    return inst


@router.post("", response_model=InstrumentOut, status_code=201)
async def create_instrument(
    payload: InstrumentIn, session: AsyncSession = Depends(get_session)
) -> Instrument:
    existing = await session.scalar(
        select(Instrument).where(Instrument.isin == payload.isin)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="ISIN already exists")
    inst = Instrument(**payload.model_dump())
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    return inst
