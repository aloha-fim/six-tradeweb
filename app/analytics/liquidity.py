"""Liquidity-intelligence signal engine (Model B: SIX-native proxies).

Pure functions over a time series of a *liquidity* measure (top-of-book
bid/ask spread, in bp). Dislocation (z-score) and drift are computed on the
series; instruments roll up to a sector stress index and a "Liquidity GPS"
cross-sector view. Everything here is explainable and reproducible -- no
black-box scoring -- consistent with how an evaluated-pricing model must be
defensible to a client.

These signals are proxies built from data SIX already sees. The strongest
signals (fill probability, dealer-inventory skew) require Dealerweb flow and
are intentionally NOT claimed here.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

Z_WINDOW = 63       # ~ one quarter of trading days
DRIFT_LOOKBACK = 20  # ~ one trading month


def _z_at(series: list[float], i: int, window: int = Z_WINDOW) -> float:
    lo = max(0, i - window + 1)
    win = series[lo:i + 1]
    if len(win) < 2:
        return 0.0
    sd = statistics.pstdev(win)
    if sd == 0:
        return 0.0
    return (series[i] - statistics.fmean(win)) / sd


def latest_z(series: list[float], window: int = Z_WINDOW) -> float:
    if not series:
        return 0.0
    return round(_z_at(series, len(series) - 1, window), 3)


def drift(series: list[float], window: int = Z_WINDOW,
          lookback: int = DRIFT_LOOKBACK) -> float:
    """Momentum of dislocation: z now minus z `lookback` points ago."""
    n = len(series)
    if n <= lookback:
        return 0.0
    return round(_z_at(series, n - 1, window) - _z_at(series, n - 1 - lookback, window), 3)


def stress_score(z: float, dr: float) -> int:
    """0-100 liquidity-stress score. 50 is neutral; higher = more stressed.

    A wider-than-normal spread (positive z) and accelerating widening
    (positive drift) both raise stress.
    """
    return int(max(0, min(100, round(50 + 18 * z + 10 * dr))))


def regime(z: float, hi: float = 0.8) -> str:
    if z >= hi:
        return "stressed"
    if z <= -hi:
        return "easing"
    return "normal"


def stretch_label(z: float) -> str:
    s = max(0.0, z)
    return "High" if s >= 1.5 else "Medium" if s >= 0.5 else "Low"


def drift_label(dr: float) -> str:
    return "Rising" if dr >= 0.5 else "Falling" if dr <= -0.5 else "Stable"


def risk_label(score: int) -> str:
    return "High" if score >= 68 else "Medium" if score >= 45 else "Low"


@dataclass(slots=True)
class SectorGPS:
    sector: str
    instruments: int
    z: float
    drift: float
    stress: int
    stretch: str
    drift_dir: str
    risk: str
    regime: str
    series: list[float]


def _aggregate(series_list: list[list[float]]) -> list[float]:
    """Element-wise mean across equal-length instrument series."""
    if not series_list:
        return []
    n = min(len(s) for s in series_list)
    return [statistics.fmean(s[i] for s in series_list) for i in range(n)]


def sector_gps(sector: str, series_list: list[list[float]],
               sparkline_points: int = 40) -> SectorGPS:
    agg = _aggregate(series_list)
    z = latest_z(agg)
    dr = drift(agg)
    score = stress_score(z, dr)
    step = max(1, len(agg) // sparkline_points)
    spark = [round(v, 2) for v in agg[::step]][-sparkline_points:]
    return SectorGPS(
        sector=sector, instruments=len(series_list), z=z, drift=dr, stress=score,
        stretch=stretch_label(z), drift_dir=drift_label(dr), risk=risk_label(score),
        regime=regime(z), series=spark,
    )


def overall_stress(gps: list[SectorGPS]) -> int:
    if not gps:
        return 0
    return int(round(statistics.fmean(g.stress for g in gps)))


def interpret(gps: list[SectorGPS]) -> str:
    if not gps:
        return "No data."
    rising = [g for g in gps if g.drift >= 0.5]
    worst = max(gps, key=lambda g: g.stress)
    if worst.stress >= 68 and worst.drift_dir == "Rising":
        return f"Elevated stress building in {worst.sector}."
    if rising:
        names = ", ".join(g.sector for g in sorted(rising, key=lambda g: -g.drift))
        return f"Liquidity tightening across {names}; nothing extreme yet."
    if worst.stress >= 68:
        return f"{worst.sector} stretched but stabilising."
    return "Liquidity broadly stable across covered sectors."
