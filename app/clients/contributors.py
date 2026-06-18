"""Synthetic multi-source contributor marks, with per-contributor reliability.

SIX sits between Tradeweb and many bank clients, so it can see where those banks
*carry* a bond. Each contributor has a reliability score (how well its past marks
have tracked executed trades); less-reliable contributors submit noisier marks and
occasionally a stale outlier, which the consensus engine's robust filter removes.

The eval's error vs the consensus still decomposes into a learnable systematic
sector bias plus idiosyncratic noise (see consensus / eval_harness).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass

# (contributor, baseline reliability) -- reliability drives Bayesian weight and noise.
_CONTRIBUTORS = [
    ("Bank A", 0.96), ("Bank B", 0.92), ("Bank C", 0.86),
    ("Bank D", 0.78), ("Bank E", 0.70),
]
# Live reliability is mutable: executed-trade recalibration updates it in place,
# so a correction actually feeds forward into the next consensus (closed loop).
_BASELINE_RELIABILITY = {name: rel for name, rel in _CONTRIBUTORS}
_LIVE_RELIABILITY = dict(_BASELINE_RELIABILITY)


def baseline_reliability() -> dict[str, float]:
    return dict(_BASELINE_RELIABILITY)


def get_reliability() -> dict[str, float]:
    return dict(_LIVE_RELIABILITY)


def set_reliability(name: str, value: float) -> None:
    if name in _LIVE_RELIABILITY:
        _LIVE_RELIABILITY[name] = round(max(0.30, min(0.999, value)), 4)


def reset_reliability() -> None:
    _LIVE_RELIABILITY.clear()
    _LIVE_RELIABILITY.update(_BASELINE_RELIABILITY)

_SECTOR_BIAS = {"GO": 0.05, "REVENUE": 0.22}


@dataclass(slots=True)
class ContributorMark:
    contributor: str
    price: float
    reliability: float
    confidence: float
    age_hours: float = 0.0       # how stale this mark is -> time-decay weighting


def _rng(cusip: str, day: str) -> random.Random:
    seed = int(hashlib.sha256(f"{cusip}|{day}|consensus".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def systematic_bias(sector: str) -> float:
    return _SECTOR_BIAS.get(sector, 0.05)


def contributor_marks(cusip: str, ai_price: float, sector: str,
                      liquidity_score: float, as_of: dt.datetime) -> list[ContributorMark]:
    r = _rng(cusip, as_of.date().isoformat())
    illiquidity = max(0.0, (100.0 - liquidity_score) / 100.0)
    dispersion = 0.04 + illiquidity * 0.30
    idiosyncratic = illiquidity * r.uniform(-0.25, 0.25)
    consensus_true = float(ai_price) - (systematic_bias(sector) + idiosyncratic)
    # ~1 in 3 bonds carries a stale outlier from the least-reliable contributor
    inject = int(hashlib.sha256(f"{cusip}|outlier".encode()).hexdigest()[:4], 16) % 3 == 0
    marks = []
    for name, rel in _CONTRIBUTORS:
        noise = r.gauss(0, dispersion * (1.5 - rel))           # less reliable => noisier
        price = consensus_true + noise
        if inject and name == "Bank E":
            price = consensus_true + (0.9 if r.random() < 0.5 else -0.9)   # stale outlier
        conf = round(min(0.99, max(0.50, rel + r.uniform(-0.05, 0.05))), 3)
        # less-reliable desks mark less often -> staler marks; illiquid names stale faster
        age_hours = round(r.uniform(0, 8) + (1.0 - rel) * 48 + illiquidity * 36, 1)
        marks.append(ContributorMark(name, round(price, 4), rel, conf, age_hours))
    return marks
