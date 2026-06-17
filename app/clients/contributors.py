"""Synthetic multi-source contributor marks.

SIX sits between Tradeweb and many bank clients, so it can see where those banks
*carry* a bond and blend them into a consensus. Comparing Ai-Price to that
consensus is a model-quality signal Tradeweb cannot self-produce.

The eval's error vs the consensus has two parts, mirroring how real evaluated
models behave:
  - a SYSTEMATIC sector bias (e.g. the model runs rich in revenue bonds) -- this
    is the learnable part that aggregated feedback can correct and generalise;
  - an IDIOSYNCRATIC per-bond part that scales with illiquidity -- noise that
    feedback cannot generalise away.

Marks are fabricated deterministically so the method can be demonstrated; in
production these are real client-carried marks under the appropriate permissions.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass

_CONTRIBUTORS = ["Bank A", "Bank B", "Bank C", "Bank D", "Bank E"]

# Systematic richness of Ai-Price vs where the Street carries it, by sector
# (price points). Positive = eval marks higher (richer) than consensus. This is
# the learnable part; it dominates so aggregated feedback can recover it.
_SECTOR_BIAS = {"GO": 0.05, "REVENUE": 0.22}


@dataclass(slots=True)
class ContributorMark:
    contributor: str
    price: float


def _rng(cusip: str, day: str) -> random.Random:
    seed = int(hashlib.sha256(f"{cusip}|{day}|consensus".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def systematic_bias(sector: str) -> float:
    return _SECTOR_BIAS.get(sector, 0.05)


def contributor_marks(cusip: str, ai_price: float, sector: str,
                      liquidity_score: float, as_of: dt.datetime) -> list[ContributorMark]:
    r = _rng(cusip, as_of.date().isoformat())
    illiquidity = max(0.0, (100.0 - liquidity_score) / 100.0)   # 0 (liquid) .. ~0.6
    dispersion = 0.04 + illiquidity * 0.30                       # bank disagreement (price pts)
    idiosyncratic = illiquidity * r.uniform(-0.25, 0.25)        # un-learnable noise
    bias = systematic_bias(sector) + idiosyncratic              # eval sits this far off the book
    consensus_true = float(ai_price) - bias
    return [ContributorMark(name, round(consensus_true + r.gauss(0, dispersion), 4))
            for name in _CONTRIBUTORS]
