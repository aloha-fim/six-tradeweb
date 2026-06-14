"""Synthetic multi-source contributor marks.

SIX sits between Tradeweb and many bank clients, so it can see where those banks
*carry* a bond on their own books and blend them into a consensus. Comparing the
Ai-Price evaluation to that consensus is a strong, trader-relevant model-quality
signal that Tradeweb cannot self-produce -- it needs the multi-source vantage.

This module fabricates contributor marks deterministically so the method can be
demonstrated. In production these would be real client-carried marks ingested
under the appropriate permissions -- the multi-source ingest kept pending
elsewhere. Banks disagree more on illiquid bonds, and the evaluation can sit
off the consensus, more so when the bond is illiquid.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass

_CONTRIBUTORS = ["Bank A", "Bank B", "Bank C", "Bank D", "Bank E"]


@dataclass(slots=True)
class ContributorMark:
    contributor: str
    price: float


def _rng(cusip: str, day: str) -> random.Random:
    seed = int(hashlib.sha256(f"{cusip}|{day}|consensus".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def contributor_marks(cusip: str, ai_price: float, liquidity_score: float,
                      as_of: dt.datetime) -> list[ContributorMark]:
    r = _rng(cusip, as_of.date().isoformat())
    illiquidity = max(0.0, (100.0 - liquidity_score) / 100.0)   # 0 (liquid) .. ~0.6
    dispersion = 0.05 + illiquidity * 0.45                       # bank disagreement (price pts)
    bias = illiquidity * r.uniform(-1.2, 1.2)                    # eval may sit off the book
    consensus_true = float(ai_price) - bias
    return [ContributorMark(name, round(consensus_true + r.gauss(0, dispersion), 4))
            for name in _CONTRIBUTORS]
