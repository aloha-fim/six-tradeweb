"""Pydantic v2 schemas."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .models import AssetClass, MuniSector, PriceType, RatesProduct


class InstrumentIn(BaseModel):
    isin: str = Field(min_length=12, max_length=12)
    symbol: str
    name: str
    asset_class: AssetClass
    currency: str = "CHF"
    venue: str = "SIX Swiss Exchange"


class InstrumentOut(InstrumentIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: dt.datetime


class FixedIncomeQuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    instrument_id: int
    clean_price: float
    yield_to_maturity: float
    source: str
    as_of: dt.datetime


class AiPriceQuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cusip: str
    description: str
    state: str
    sector: MuniSector
    rating_sp: str
    coupon: float
    maturity: dt.date
    callable: bool
    call_date: dt.date | None
    size_outstanding_mm: float
    price_type: PriceType
    eval_bid: float
    ai_price: float
    eval_ask: float
    price_change_1d: float
    ai_yield: float
    yield_to_worst: float
    yield_to_call: float | None
    benchmark_spread_bp: float
    ust_spread_bp: float
    effective_duration: float
    convexity: float
    dv01: float
    liquidity_score: float
    trades_30d: int
    last_trade_date: dt.date | None
    confidence: float
    model_version: str
    as_of: dt.datetime


class AiPriceRefreshResult(BaseModel):
    ingested: int
    as_of: dt.datetime
    model_version: str
    price_type: PriceType
    source: str = "Tradeweb Municipal Ai-Price"


class DealerwebQuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product: RatesProduct
    instrument: str
    tenor: str
    coupon: float | None
    bid: float
    ask: float
    mid: float
    bid_size_mm: float
    ask_size_mm: float
    spread_bp: float
    liquidity_score: float
    source: str
    as_of: dt.datetime


class HoldingIn(BaseModel):
    cusip: str = Field(min_length=9, max_length=9)
    par_amount: float = Field(gt=0)


class PortfolioIn(BaseModel):
    name: str
    holdings: list[HoldingIn] = []


class HoldingOut(HoldingIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    created_at: dt.datetime
    holdings: list[HoldingOut]
