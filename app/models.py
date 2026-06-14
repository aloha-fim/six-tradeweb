"""ORM models.

Reflects the real SIX <-> Tradeweb relationship and the expanded analytics scope:

  * ``Instrument``         -- SIX Swiss Exchange listings (SIX's own business).
  * ``FixedIncomeQuote``   -- Tradeweb evaluated FI prices SIX redistributes.
  * ``AiPriceQuote``       -- Tradeweb Municipal Ai-Price: rich evaluated muni
                              record (bid/mid/ask, curve spread, risk, rating,
                              liquidity, confidence). SIX has distributed this
                              product since 2022.
  * ``DealerwebQuote``     -- Inter-dealer top-of-book for UST / TBA-MBS. NOTE:
                              SIX does not distribute Dealerweb today; modelled
                              here as a *prospective* data product.
  * ``Portfolio``/``Holding`` -- client positions for valuation + risk analytics.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class AssetClass(str, enum.Enum):
    EQUITY = "equity"
    ETF = "etf"
    BOND = "bond"


class MuniSector(str, enum.Enum):
    GO = "GO"            # general obligation
    REVENUE = "REVENUE"  # revenue bond


class PriceType(str, enum.Enum):
    EOD = "EOD"
    INTRADAY = "INTRADAY"


class RatesProduct(str, enum.Enum):
    UST = "UST"          # US Treasury (on/off-the-run)
    TBA_MBS = "TBA_MBS"  # to-be-announced agency MBS


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(160))
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass))
    currency: Mapped[str] = mapped_column(String(3), default="CHF")
    venue: Mapped[str] = mapped_column(String(32), default="SIX Swiss Exchange")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    fi_quotes: Mapped[list["FixedIncomeQuote"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class FixedIncomeQuote(Base):
    __tablename__ = "fi_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    clean_price: Mapped[float] = mapped_column(Numeric(12, 4))
    yield_to_maturity: Mapped[float] = mapped_column(Numeric(8, 4))
    source: Mapped[str] = mapped_column(String(32), default="Tradeweb")
    as_of: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    instrument: Mapped[Instrument] = relationship(back_populates="fi_quotes")


class AiPriceQuote(Base):
    """A rich Tradeweb Municipal Ai-Price evaluated record."""

    __tablename__ = "ai_price_quotes"
    __table_args__ = (Index("ix_ai_price_cusip_asof", "cusip", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- reference ----------------------------------------------------------
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    description: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(2), index=True)
    sector: Mapped[MuniSector] = mapped_column(Enum(MuniSector), index=True)
    rating_sp: Mapped[str] = mapped_column(String(4))
    coupon: Mapped[float] = mapped_column(Numeric(6, 3))
    maturity: Mapped[dt.date] = mapped_column(Date)
    callable: Mapped[bool] = mapped_column(Boolean, default=False)
    call_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    size_outstanding_mm: Mapped[float] = mapped_column(Numeric(12, 2))

    # --- evaluated pricing (the Ai-Price model output) ----------------------
    price_type: Mapped[PriceType] = mapped_column(Enum(PriceType), default=PriceType.EOD)
    eval_bid: Mapped[float] = mapped_column(Numeric(12, 4))
    ai_price: Mapped[float] = mapped_column(Numeric(12, 4))  # evaluated mid
    eval_ask: Mapped[float] = mapped_column(Numeric(12, 4))
    price_change_1d: Mapped[float] = mapped_column(Float, default=0.0)

    # --- yield / spread -----------------------------------------------------
    ai_yield: Mapped[float] = mapped_column(Numeric(8, 4))    # yield to maturity
    yield_to_worst: Mapped[float] = mapped_column(Numeric(8, 4))
    yield_to_call: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    benchmark_spread_bp: Mapped[float] = mapped_column(Float)  # vs AAA muni curve
    ust_spread_bp: Mapped[float] = mapped_column(Float)        # vs UST curve

    # --- risk ---------------------------------------------------------------
    effective_duration: Mapped[float] = mapped_column(Float)
    convexity: Mapped[float] = mapped_column(Float)
    dv01: Mapped[float] = mapped_column(Float)  # price pts per 1bp per 100 face

    # --- liquidity / model --------------------------------------------------
    liquidity_score: Mapped[float] = mapped_column(Float)  # 0-100
    trades_30d: Mapped[int] = mapped_column(Integer, default=0)
    last_trade_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)        # 0-1 model confidence
    model_version: Mapped[str] = mapped_column(String(24), default="aiprice-2.x")
    as_of: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DealerwebQuote(Base):
    """Inter-dealer top-of-book (UST / TBA-MBS). Prospective SIX data product."""

    __tablename__ = "dealerweb_quotes"
    __table_args__ = (Index("ix_dw_instr_asof", "instrument", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[RatesProduct] = mapped_column(Enum(RatesProduct), index=True)
    instrument: Mapped[str] = mapped_column(String(48), index=True)
    tenor: Mapped[str] = mapped_column(String(16))     # e.g. "10Y", "30Y"
    coupon: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)  # MBS
    bid: Mapped[float] = mapped_column(Numeric(12, 4))
    ask: Mapped[float] = mapped_column(Numeric(12, 4))
    mid: Mapped[float] = mapped_column(Numeric(12, 4))
    bid_size_mm: Mapped[float] = mapped_column(Numeric(12, 2))
    ask_size_mm: Mapped[float] = mapped_column(Numeric(12, 2))
    spread_bp: Mapped[float] = mapped_column(Float)        # bid/ask in bp
    liquidity_score: Mapped[float] = mapped_column(Float)  # 0-100
    source: Mapped[str] = mapped_column(String(32), default="Dealerweb")
    as_of: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    holdings: Mapped[list["Holding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    par_amount: Mapped[float] = mapped_column(Numeric(16, 2))  # face value

    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")


class UsageEvent(Base):
    """A client consumption event (the flywheel's 'consume' step)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="view")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class PriceChallenge(Base):
    """A client disputing an evaluated price (the 'validate' step)."""

    __tablename__ = "price_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    client: Mapped[str] = mapped_column(
        String(40), default="unspecified", server_default="unspecified", index=True
    )  # which SIX bank client raised the challenge
    observed_price: Mapped[float] = mapped_column(Numeric(12, 4))   # price the client saw
    challenged_price: Mapped[float] = mapped_column(Numeric(12, 4)) # what they argue
    note: Mapped[str] = mapped_column(String(240), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    settled_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelAdjustment(Base):
    """An accepted correction fed back to the model (the 'retrain' step)."""

    __tablename__ = "model_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    price_delta: Mapped[float] = mapped_column(Float)  # settled - observed (price points)
    challenge_id: Mapped[int | None] = mapped_column(
        ForeignKey("price_challenges.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
