"""Issuer-sector liquidity graph: structure and propagation."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.analytics.curve import fit_svensson
from app.analytics.liquidity_graph import build_graph, maturity_bucket, propagate


def _rec(cusip, state, sector, rating, years, liq, yld, coupon=5.0):
    as_of = dt.datetime(2026, 6, 16, tzinfo=dt.timezone.utc)
    return SimpleNamespace(cusip=cusip, state=state,
                           sector=SimpleNamespace(value=sector), rating_sp=rating,
                           maturity=as_of.date() + dt.timedelta(days=int(years * 365.25)),
                           as_of=as_of, liquidity_score=liq, ai_yield=yld,
                           coupon=coupon, ai_price=100.0)


def test_maturity_buckets():
    assert maturity_bucket(2) == "0-3Y" and maturity_bucket(10) == "7-15Y"


def test_graph_structure():
    recs = [_rec("AAA111111", "CA", "GO", "AA", 10, 80, 4.0),
            _rec("BBB222222", "CA", "REVENUE", "A", 5, 40, 4.2)]
    bonds, nodes, edges = build_graph(recs)
    # each bond links to its state/sector/rating/maturity node
    assert len([e for e in edges if e["source"] == "bond:AAA111111"]) == 4
    assert any(n["id"] == "state:CA" and n["type"] == "state" for n in nodes)


def test_illiquid_bond_borrows_from_neighbors():
    # two CA bonds: one liquid, one illiquid -> they share the state node
    recs = [_rec("LIQ111111", "CA", "GO", "AA", 10, 90, 4.0),
            _rec("ILL222222", "CA", "GO", "AA", 11, 20, 4.1),
            _rec("OTH333333", "NY", "REVENUE", "BBB", 3, 50, 3.5)]
    bonds, _, _ = build_graph(recs)
    params = fit_svensson([b["years"] for b in bonds], [b["ai_yield"] for b in bonds])
    metrics = {m["cusip"]: m for m in propagate(bonds, params)}
    ill = metrics["ILL222222"]
    # the illiquid CA bond is pulled up toward its liquid CA neighbour
    assert ill["neighbor_count"] >= 1
    assert ill["network_liquidity"] > ill["own_liquidity"]
    assert ill["borrowed_price_anchor"] is not None
    # its strongest neighbour is the other CA/GO/AA bond
    assert ill["neighbors"][0]["cusip"] == "LIQ111111"


async def test_graph_endpoint(client):
    await client.post("/ai-price/refresh")
    g = (await client.get("/graph/liquidity")).json()
    assert g["counts"]["bonds"] == 12
    assert g["counts"]["nodes"] > 12 and g["counts"]["edges"] == 12 * 4
    assert all("network_liquidity" in b for b in g["bonds"])
