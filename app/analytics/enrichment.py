"""Enrichment engine: assemble the SIX bundled product automatically.

Takes a priced instrument (Tradeweb evaluated price/yield) and joins it to:
  * resolved identity   -- CUSIP <-> ISIN (with a real check digit) <-> issuer
  * SIX risk-free rates -- the appropriate curve, interpolated at the bond's tenor
  * corporate actions   -- the call-adjusted (yield-to-worst) measure
producing one analytics-ready record with provenance. Pure functions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .muni import tax_equivalent_yield


def _luhn_isin_check(body: str) -> str:
    """Standard ISIN check digit over the converted alphanumeric body."""
    digits = ""
    for ch in body:
        digits += ch if ch.isdigit() else str(ord(ch.upper()) - 55)  # A=10..Z=35
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:          # double every second digit from the right
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def isin_from_cusip(cusip: str, country: str = "US") -> str:
    body = country + cusip
    return body + _luhn_isin_check(body)


def mock_lei(issuer: str) -> str:
    """Deterministic synthetic LEI (20 chars). Real LEIs come from GLEIF/SIX."""
    h = hashlib.sha256(issuer.encode()).hexdigest().upper()
    return "5493" + h[:14] + "00"


def interpolate(points, years: float) -> float:
    """Linear interpolation of a sorted curve (list of objects with .years/.rate)."""
    if not points:
        return 0.0
    if years <= points[0].years:
        return points[0].rate
    if years >= points[-1].years:
        return points[-1].rate
    for a, b in zip(points, points[1:]):
        if a.years <= years <= b.years:
            w = (years - a.years) / (b.years - a.years)
            return round(a.rate + w * (b.rate - a.rate), 4)
    return points[-1].rate


@dataclass(slots=True)
class EnrichedMuni:
    cusip: str
    isin: str
    lei: str
    canonical_name: str
    sector: str
    rating_sp: str
    effective_duration: float
    ai_price: float
    ai_yield: float
    price_type: str
    confidence: float
    callable: bool
    ca_adjusted_yield: float           # yield-to-worst when callable
    benchmark: str
    anchor_years: float
    risk_free_rate: float
    spread_to_riskfree_bp: float       # pre-tax (muni < UST => negative)
    tax_equivalent_yield: float
    tax_equivalent_spread_bp: float    # TEY vs risk-free (the sellable number)
    provenance: dict


def enrich_muni(r, curve, marginal_rate: float = 0.37) -> EnrichedMuni:
    """Bundle one Ai-Price muni record against a SIX risk-free curve."""
    years = max((r.maturity - r.as_of.date()).days / 365.25, 0.25)
    rf = interpolate(curve.points, years)
    ai_yield = float(r.ai_yield)
    tey = tax_equivalent_yield(ai_yield, marginal_rate)
    sector = getattr(r.sector, "value", str(r.sector))
    price_type = getattr(r.price_type, "value", str(r.price_type))
    return EnrichedMuni(
        cusip=r.cusip, isin=isin_from_cusip(r.cusip), lei=mock_lei(r.description),
        canonical_name=r.description, sector=sector, rating_sp=r.rating_sp,
        effective_duration=float(r.effective_duration),
        ai_price=float(r.ai_price), ai_yield=ai_yield, price_type=price_type,
        confidence=float(r.confidence), callable=bool(r.callable),
        ca_adjusted_yield=float(r.yield_to_worst),
        benchmark=f"{curve.currency} {curve.benchmark}", anchor_years=round(years, 2),
        risk_free_rate=rf,
        spread_to_riskfree_bp=round((ai_yield - rf) * 100, 1),
        tax_equivalent_yield=tey,
        tax_equivalent_spread_bp=round((tey - rf) * 100, 1),
        provenance={
            "price": "Tradeweb Ai-Price", "rates": "SIX (benchmark curve)",
            "identity": "SIX reference data", "corporate_actions": "SIX",
        },
    )


@dataclass(slots=True)
class EnrichedBond:
    isin: str
    symbol: str
    name: str
    currency: str
    clean_price: float
    ytm: float
    benchmark: str
    anchor_years: float
    risk_free_rate: float
    spread_to_benchmark_bp: float
    provenance: dict


def enrich_bond(instrument, quote, curve, years: float) -> EnrichedBond:
    """Bundle a SIX-listed CHF bond against the SARON-anchored curve."""
    rf = interpolate(curve.points, years)
    ytm = float(quote.yield_to_maturity)
    return EnrichedBond(
        isin=instrument.isin, symbol=instrument.symbol, name=instrument.name,
        currency=instrument.currency, clean_price=float(quote.clean_price), ytm=ytm,
        benchmark=f"{curve.currency} {curve.benchmark}", anchor_years=round(years, 2),
        risk_free_rate=rf, spread_to_benchmark_bp=round((ytm - rf) * 100, 1),
        provenance={
            "price": "Tradeweb FI", "rates": "SIX (SARON curve)",
            "identity": "SIX reference data",
        },
    )
