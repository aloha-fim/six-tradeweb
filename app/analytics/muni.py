"""Municipal analytics derived from Tradeweb Ai-Price records.

These are pure functions over any object exposing the Ai-Price attributes
(ORM ``AiPriceQuote`` rows or client ``AiPriceRecord`` dataclasses), so they
can be unit-tested without a database.
"""
from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass
from typing import Protocol


class _Muni(Protocol):
    cusip: str
    state: str
    sector: object
    rating_sp: str
    ai_yield: float
    benchmark_spread_bp: float
    effective_duration: float
    confidence: float
    liquidity_score: float
    trades_30d: int


class RvSignal(str, enum.Enum):
    CHEAP = "cheap"
    FAIR = "fair"
    RICH = "rich"


# Indicative expected-spread coefficients (bp). Transparent and explainable,
# in keeping with Ai-Price's reproducible-model design.
_RATING_BASE_BP = {
    "AAA": 5, "AA+": 12, "AA": 20, "AA-": 28,
    "A+": 40, "A": 55, "A-": 70, "BBB+": 110, "BBB": 150,
}
_SECTOR_ADJ_BP = {"REVENUE": 8, "GO": 0}
_DURATION_SLOPE_BP = 2.0   # extra spread per year of duration
_RV_THRESHOLD_BP = 10.0    # |residual| beyond this flags rich/cheap


def _sector_str(sector: object) -> str:
    return getattr(sector, "value", str(sector))


def tax_equivalent_yield(muni_yield: float, marginal_rate: float = 0.37) -> float:
    """Tax-equivalent yield for a tax-exempt muni.

    TEY = muni_yield / (1 - marginal_rate). ``marginal_rate`` is a single
    combined rate; in-state double-exemption is left to the caller to fold in.
    """
    if not 0.0 <= marginal_rate < 1.0:
        raise ValueError("marginal_rate must be in [0, 1)")
    return round(muni_yield / (1.0 - marginal_rate), 4)


def _expected_spread_bp(m: _Muni) -> float:
    base = _RATING_BASE_BP.get(m.rating_sp, 60)
    base += _SECTOR_ADJ_BP.get(_sector_str(m.sector), 0)
    base += _DURATION_SLOPE_BP * m.effective_duration
    return base


@dataclass(slots=True)
class RvRow:
    cusip: str
    actual_spread_bp: float
    expected_spread_bp: float
    residual_bp: float
    rv_percentile: float
    signal: RvSignal


def relative_value(records: list[_Muni]) -> list[RvRow]:
    """Rich/cheap screen: actual curve spread vs an explainable expected spread.

    Positive residual (wider than expected) => cheap (higher yield for the risk);
    negative => rich. Percentile ranks residuals across the supplied universe.
    """
    if not records:
        return []
    resid = {m.cusip: m.benchmark_spread_bp - _expected_spread_bp(m) for m in records}
    ordered = sorted(resid.values())
    n = len(ordered)
    out: list[RvRow] = []
    for m in records:
        r = resid[m.cusip]
        rank = sum(1 for v in ordered if v <= r)
        pct = round(100.0 * rank / n, 1)
        if r >= _RV_THRESHOLD_BP:
            sig = RvSignal.CHEAP
        elif r <= -_RV_THRESHOLD_BP:
            sig = RvSignal.RICH
        else:
            sig = RvSignal.FAIR
        out.append(RvRow(
            cusip=m.cusip,
            actual_spread_bp=round(m.benchmark_spread_bp, 1),
            expected_spread_bp=round(_expected_spread_bp(m), 1),
            residual_bp=round(r, 1),
            rv_percentile=pct,
            signal=sig,
        ))
    out.sort(key=lambda x: x.residual_bp, reverse=True)
    return out


def market_summary(records: list[_Muni], marginal_rate: float = 0.37) -> dict:
    """Market-level analytics across an Ai-Price snapshot."""
    if not records:
        return {"count": 0}

    yields = [m.ai_yield for m in records]
    spreads = [m.benchmark_spread_bp for m in records]
    durations = [m.effective_duration for m in records]
    conf = [m.confidence for m in records]
    illiquid = sum(1 for m in records if m.trades_30d == 0)

    def _bucket(key_fn):
        b: dict[str, int] = {}
        for m in records:
            k = key_fn(m)
            b[k] = b.get(k, 0) + 1
        return dict(sorted(b.items()))

    avg_yield = round(statistics.fmean(yields), 4)
    return {
        "count": len(records),
        "avg_yield": avg_yield,
        "avg_tax_equivalent_yield": tax_equivalent_yield(avg_yield, marginal_rate),
        "marginal_rate": marginal_rate,
        "avg_benchmark_spread_bp": round(statistics.fmean(spreads), 1),
        "avg_effective_duration": round(statistics.fmean(durations), 3),
        "avg_confidence": round(statistics.fmean(conf), 3),
        "illiquid_count": illiquid,
        "illiquid_pct": round(100.0 * illiquid / len(records), 1),
        "by_state": _bucket(lambda m: m.state),
        "by_sector": _bucket(lambda m: _sector_str(m.sector)),
        "by_rating": _bucket(lambda m: m.rating_sp),
    }
