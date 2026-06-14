"""Unit tests for the analytics functions (no DB)."""
from __future__ import annotations

import datetime as dt

from app.analytics import (
    market_summary,
    relative_value,
    tax_equivalent_yield,
    value_portfolio,
)
from app.clients.tradeweb import _MUNI_UNIVERSE, _build_record


def _records():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    return [_build_record(r, now, intraday=False) for r in _MUNI_UNIVERSE]


def test_tey_formula():
    assert tax_equivalent_yield(3.0, 0.40) == 5.0
    assert tax_equivalent_yield(4.0, 0.0) == 4.0


def test_tey_rejects_bad_rate():
    import pytest
    with pytest.raises(ValueError):
        tax_equivalent_yield(3.0, 1.0)


def test_market_summary_shape():
    s = market_summary(_records(), 0.37)
    assert s["count"] == 12
    assert s["avg_tax_equivalent_yield"] > s["avg_yield"]
    assert sum(s["by_state"].values()) == 12


def test_relative_value_signals_and_sort():
    rv = relative_value(_records())
    assert len(rv) == 12
    assert rv == sorted(rv, key=lambda x: x.residual_bp, reverse=True)
    assert {r.signal.value for r in rv} <= {"cheap", "fair", "rich"}


def test_portfolio_weights_sum_to_one():
    recs = {r.cusip: r for r in _records()}
    holdings = [("13063DAB7", 1_000_000.0), ("452152AR7", 500_000.0)]
    v = value_portfolio(holdings, recs, 0.37)
    assert v["positions"] == 2
    assert abs(sum(v["sector_weights"].values()) - 1.0) < 1e-6
    assert v["weighted_tax_equivalent_yield"] > v["weighted_yield"]
    assert v["portfolio_dv01"] > 0
