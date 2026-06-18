"""Liquidity graph endpoints: the issuer/sector network and per-bond propagation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics.curve import fit_svensson
from ..analytics.liquidity_graph import build_graph, propagate
from ..db import get_session
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Liquidity graph"])


@router.get("/graph/liquidity")
async def liquidity_graph(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    bonds, nodes, edges = build_graph(rows)
    params = fit_svensson([b["years"] for b in bonds], [b["ai_yield"] for b in bonds])
    metrics = propagate(bonds, params)
    return {
        "node_types": ["bond", "state", "sector", "rating", "maturity"],
        "edge_weights": {"state": 0.50, "sector": 0.20, "rating": 0.15, "maturity": 0.15},
        "counts": {"nodes": len(nodes), "edges": len(edges), "bonds": len(bonds)},
        "method": ("bonds linked to issuer(state)/sector/rating/maturity nodes; an illiquid bond "
                   "borrows neighbours' liquidity and their spread-to-curve, re-priced on the "
                   "fitted curve as a borrowed anchor"),
        "nodes": nodes, "edges": edges, "bonds": metrics,
    }


@router.get("/graph/liquidity/{cusip}")
async def liquidity_graph_one(cusip: str, session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    bonds, _, _ = build_graph(rows)
    if not any(b["cusip"] == cusip for b in bonds):
        raise HTTPException(status_code=404, detail="Unknown CUSIP")
    params = fit_svensson([b["years"] for b in bonds], [b["ai_yield"] for b in bonds])
    metrics = propagate(bonds, params)
    return next(m for m in metrics if m["cusip"] == cusip)
