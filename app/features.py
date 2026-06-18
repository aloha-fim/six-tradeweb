"""Feature engineering -- one canonical, versioned, leakage-safe extractor.

Training/serving parity: `build_features` is the single source of features used by
both the offline store (materialize) and the backtest, so live pricing and
backtesting never diverge silently. Leakage prevention: it reads only fields known
as of the record's own timestamp -- never a future trade or a post-event price.

Offline only (Postgres), per the stack constraint: no online (Redis/Dynamo) layer.
"""
from __future__ import annotations

FEATURE_SET_VERSION = "bond_features_v1"

_RATING = {
    "AAA": 1.00, "AA+": 0.95, "AA": 0.90, "AA-": 0.85, "A+": 0.80, "A": 0.75,
    "A-": 0.70, "BBB+": 0.62, "BBB": 0.56, "BBB-": 0.50, "BB+": 0.42, "BB": 0.36,
}


def rating_score(rating_sp: str | None) -> float:
    return _RATING.get((rating_sp or "").strip(), 0.50)


def volatility_30d(liquidity_score: float) -> float:
    """Synthetic 30d price-vol proxy from liquidity (illiquid -> more volatile).

    A real store would compute this from the stored trade/price history; here it is
    a deterministic, leakage-free proxy.
    """
    illiquidity = max(0.0, (100.0 - float(liquidity_score)) / 100.0)
    return round(0.05 + illiquidity * 0.60, 4)


def build_features(record) -> dict:
    """Point-in-time feature vector for one bond. Reads only as-of-known fields."""
    sector = getattr(record.sector, "value", str(record.sector))
    return {
        "cusip": record.cusip,
        "as_of": record.as_of,
        "feature_set_version": FEATURE_SET_VERSION,
        "sector": sector,
        "rating_score": rating_score(record.rating_sp),
        "duration": float(record.effective_duration),
        "convexity": float(record.convexity),
        "liquidity_score": float(record.liquidity_score),
        "benchmark_spread_bp": float(record.benchmark_spread_bp),
        "trade_count_30d": int(getattr(record, "trades_30d", 0) or 0),
        "volatility_30d": volatility_30d(record.liquidity_score),
        "ai_price": float(record.ai_price),   # market feature: the Bayesian prior
    }
