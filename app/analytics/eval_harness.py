"""Loop-closure eval harness: does feedback measurably improve the prices?

Hold-out design (the credible test):
  1. Compute each bond's consensus-tracking error (Ai-Price vs the median of
     bank-client marks), in bp.
  2. Split bonds into a TRAIN set (the challenges SIX has seen) and a held-out
     EVAL set the model never saw corrected.
  3. From TRAIN, estimate the *systematic* sector bias -- the learnable signal
     SIX feeds back -- and apply it to the held-out bonds.
  4. Re-measure error on the held-out bonds. A drop proves the feedback
     generalises rather than just memorising the challenged names.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ..clients.contributors import contributor_marks
from .consensus import consensus_deviation


def _err(devs: list[float]) -> tuple[float, float]:
    n = len(devs) or 1
    mae = sum(abs(d) for d in devs) / n
    rmse = math.sqrt(sum(d * d for d in devs) / n)
    return round(mae, 2), round(rmse, 2)


def loop_closure_eval(records) -> dict:
    rows = []
    for r in records:
        marks = contributor_marks(r.cusip, float(r.ai_price), r.sector.value,
                                  float(r.liquidity_score), r.as_of)
        rows.append((r, consensus_deviation(r, marks)))

    # sector-stratified hold-out: alternate bonds within each sector into
    # train/eval, so both sectors appear in train (to learn) and in holdout.
    train, holdout = [], []
    seen: dict[str, int] = {}
    for r, cd in rows:
        k = seen.get(cd.sector, 0)
        (train if k % 2 == 0 else holdout).append((r, cd))
        seen[cd.sector] = k + 1

    # SIX feeds back the systematic sector bias learned from the train challenges
    by_sector: dict[str, list[float]] = defaultdict(list)
    for _, cd in train:
        by_sector[cd.sector].append(cd.deviation_price)
    learned = {s: round(sum(v) / len(v), 4) for s, v in by_sector.items()}

    before = [cd.deviation_bp for _, cd in holdout]
    after, detail = [], []
    for r, cd in holdout:
        corr = learned.get(cd.sector, 0.0)
        dur = float(r.effective_duration) or 1.0
        new_bp = round((cd.deviation_price - corr) / dur * 100, 1)
        after.append(new_bp)
        detail.append({"cusip": r.cusip, "sector": cd.sector,
                       "before_bp": cd.deviation_bp, "after_bp": new_bp})

    mae_b, rmse_b = _err(before)
    mae_a, rmse_a = _err(after)
    improvement = round(100 * (mae_b - mae_a) / mae_b, 1) if mae_b else 0.0
    return {
        "method": ("hold-out: learn the systematic sector bias from train challenges, "
                   "apply to held-out bonds, re-measure consensus-tracking error"),
        "train_n": len(train), "holdout_n": len(holdout),
        "learned_sector_bias_price": learned,
        "before": {"mae_bp": mae_b, "rmse_bp": rmse_b},
        "after": {"mae_bp": mae_a, "rmse_bp": rmse_a},
        "improvement_pct": improvement,
        "holdout": detail,
    }
