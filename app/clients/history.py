"""Synthetic spread-history generator for the liquidity signal engine.

Real Model-B signals would run on a stored time series of evaluated/observed
spreads. In the demo we hold only a few snapshots, so we generate a
deterministic ~90-day daily history of the top-of-book bid/ask spread (bp),
ending at the instrument's current spread, with a sector-dependent mean offset
and recent ramp so dislocation and drift are meaningful rather than flat.
"""
from __future__ import annotations

import hashlib
import random

# sector -> (mean_ratio, ramp_fraction): historical mean vs current anchor,
# and a recent linear ramp over the last DRIFT window as a fraction of anchor.
SECTOR_PROFILE = {
    "UST": (1.05, -0.14),         # calm, tightening
    "Muni GO": (1.00, 0.04),      # neutral
    "Muni Revenue": (0.88, 0.16), # elevated, widening
    "Agency MBS": (0.82, 0.30),   # stressed, widening fast
}
_DEFAULT = (0.95, 0.06)


def _rng(key: str) -> random.Random:
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def synthetic_bidask_series(key: str, anchor_bp: float, sector: str,
                            days: int = 90, ramp_days: int = 20) -> list[float]:
    anchor_bp = max(0.1, float(anchor_bp))
    mean_ratio, ramp_frac = SECTOR_PROFILE.get(sector, _DEFAULT)
    rng = _rng(key + "|" + sector)
    mean = anchor_bp * mean_ratio
    phi = 0.85
    sigma = anchor_bp * 0.06
    s = [mean]
    for _ in range(1, days):
        s.append(mean + phi * (s[-1] - mean) + rng.gauss(0, sigma))
    # recent linear ramp (sector trend)
    ramp_total = anchor_bp * ramp_frac
    for i in range(ramp_days):
        idx = days - ramp_days + i
        s[idx] += ramp_total * (i + 1) / ramp_days
    # anchor the final point to the current spread (keeps the ramp intact)
    s[-1] = anchor_bp
    return [round(max(0.1, x), 4) for x in s]


# The signal engine treats any spread series the same way; alias for clarity
# when the series is a valuation spread (e.g. after-tax spread to risk-free)
# rather than a bid/ask liquidity spread.
synthetic_spread_series = synthetic_bidask_series
