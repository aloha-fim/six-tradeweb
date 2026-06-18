"""Liquidity graph model -- an issuer/sector/rating/maturity network.

Each bond is a node connected to its issuer (state, the muni issuer-cluster), its
sector, its rating, and its maturity bucket. An illiquid bond borrows strength
from connected, better-covered bonds: its 'network liquidity' blends its own with
its neighbours', and a 'borrowed price anchor' re-prices it at the neighbour-implied
spread to the fitted curve (a maturity-neutral quantity, so it is meaningful across
bonds at different price levels). This generalises the hierarchical sector anchor in
the consensus engine to a multi-relation graph, and is most useful exactly where
direct marks are thin.

Pure Python: a small adjacency model and one-step weighted propagation, no graph
library.
"""
from __future__ import annotations

from .curve import bond_price_from_yield, curve_yield

# relative pull of each shared attribute (same issuer/state is the strongest link)
EDGE_WEIGHTS = {"state": 0.50, "sector": 0.20, "rating": 0.15, "maturity": 0.15}


def maturity_bucket(years: float) -> str:
    if years < 3:
        return "0-3Y"
    if years < 7:
        return "3-7Y"
    if years < 15:
        return "7-15Y"
    return "15Y+"


def _bond_view(record) -> dict:
    as_of = record.as_of.date() if hasattr(record.as_of, "date") else record.as_of
    years = max(0.05, (record.maturity - as_of).days / 365.25)
    return {"cusip": record.cusip, "state": record.state,
            "sector": getattr(record.sector, "value", str(record.sector)),
            "rating": record.rating_sp, "maturity": maturity_bucket(years),
            "years": round(years, 2), "liquidity": float(record.liquidity_score),
            "coupon": float(record.coupon), "ai_yield": float(record.ai_yield),
            "ai_price": float(record.ai_price)}


def build_graph(records) -> tuple[list[dict], list[dict], list[dict]]:
    bonds = [_bond_view(r) for r in records]
    nodes: list[dict] = []
    seen: set[str] = set()
    edges: list[dict] = []

    def add(nid: str, ntype: str, label: str) -> None:
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label})

    for b in bonds:
        bid = f"bond:{b['cusip']}"
        add(bid, "bond", b["cusip"])
        for typ, val in (("state", b["state"]), ("sector", b["sector"]),
                         ("rating", b["rating"]), ("maturity", b["maturity"])):
            nid = f"{typ}:{val}"
            add(nid, typ, val)
            edges.append({"source": bid, "target": nid, "type": typ})
    return bonds, nodes, edges


def _shared_weight(a: dict, b: dict) -> float:
    w = 0.0
    for attr, key in (("state", "state"), ("sector", "sector"),
                      ("rating", "rating"), ("maturity", "maturity")):
        if a[key] == b[key]:
            w += EDGE_WEIGHTS[attr]
    return w


def propagate(bonds: list[dict], curve_params: dict, *, own_weight: float = 0.6) -> list[dict]:
    out = []
    for b in bonds:
        wsum = liq_acc = sf_acc = 0.0
        nbrs = []
        for o in bonds:
            if o["cusip"] == b["cusip"]:
                continue
            w = _shared_weight(b, o)
            if w <= 0:
                continue
            o_sf = o["ai_yield"] - curve_yield(curve_params, o["years"])   # spread to fitted curve
            wsum += w
            liq_acc += w * o["liquidity"]
            sf_acc += w * o_sf
            nbrs.append({"cusip": o["cusip"], "weight": round(w, 3),
                         "liquidity": o["liquidity"]})
        if wsum > 0:
            nbr_liq = liq_acc / wsum
            nbr_sf = sf_acc / wsum
            net_liq = round(own_weight * b["liquidity"] + (1 - own_weight) * nbr_liq, 1)
            borrowed_yield = curve_yield(curve_params, b["years"]) + nbr_sf
            borrowed = round(bond_price_from_yield(100.0, b["coupon"], borrowed_yield, b["years"]), 4)
            nbr_liq_r, nbr_sf_bp = round(nbr_liq, 1), round(nbr_sf * 100, 1)
        else:
            net_liq, borrowed, nbr_liq_r, nbr_sf_bp = b["liquidity"], None, None, None
        out.append({
            "cusip": b["cusip"], "state": b["state"], "sector": b["sector"],
            "rating": b["rating"], "maturity_bucket": b["maturity"],
            "own_liquidity": b["liquidity"], "neighbor_count": len(nbrs),
            "neighbor_avg_liquidity": nbr_liq_r, "network_liquidity": net_liq,
            "neighbor_spread_to_curve_bp": nbr_sf_bp, "ai_price": b["ai_price"],
            "borrowed_price_anchor": borrowed,
            "neighbors": sorted(nbrs, key=lambda x: -x["weight"])[:5],
        })
    return out
