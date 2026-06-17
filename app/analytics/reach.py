"""Reach sizing: the distribution value of SIX, in dollars not adjectives.

Important honesty point: SIX owns the client relationships, the AUM and the
holdings -- Tradeweb cannot see stages 1-3 and could not compute this funnel
itself. So the gross downstream data value is *SIX's* number. Under the
redistribution arrangement that exists today, Tradeweb licenses Ai-Price to SIX
and captures a *contracted share* of the downstream fee, not the gross. The
panel therefore reports both: the size of the prize, and the slice Tradeweb
actually books -- which is the accurate, more persuasive framing for a Tradeweb
reader who knows they don't get the whole pool.

All inputs are illustrative assumptions, stated explicitly.
"""
from __future__ import annotations

ASSUMPTIONS = {
    "six_muni_clients": 120,            # SIX institutional clients holding/pricing munis
    "avg_muni_aum_musd": 850,           # avg muni AUM per client ($mm)
    "tradeweb_untouched_share": 0.60,   # share not currently price-touched by Tradeweb
    "annual_data_fee_bps": 0.40,        # annual evaluated-data fee (bps on priced AUM)
    "tradeweb_contracted_share": 0.30,  # Tradeweb's slice of the downstream fee (redistribution)
}


def reach_sizing(a: dict | None = None) -> dict:
    a = a or ASSUMPTIONS
    total_aum = a["six_muni_clients"] * a["avg_muni_aum_musd"]
    addressable = total_aum * a["tradeweb_untouched_share"]
    downstream = addressable * a["annual_data_fee_bps"] / 10000.0      # SIX-side gross
    tradeweb = downstream * a["tradeweb_contracted_share"]             # Tradeweb's share
    return {
        "illustrative": True,
        "assumptions": a,
        "total_client_muni_aum_musd": round(total_aum),
        "addressable_aum_musd": round(addressable),
        "downstream_data_value_musd": round(downstream, 1),           # the prize (SIX owns it)
        "tradeweb_contracted_revenue_musd": round(tradeweb, 1),       # what Tradeweb books
        "funnel": [
            {"stage": "SIX muni clients", "value": a["six_muni_clients"], "unit": "institutions", "owner": "SIX"},
            {"stage": "Total muni AUM", "value": round(total_aum), "unit": "$mm", "owner": "SIX"},
            {"stage": "Not price-touched by Tradeweb", "value": round(addressable), "unit": "$mm", "owner": "SIX"},
            {"stage": "Downstream data value", "value": round(downstream, 1), "unit": "$mm/yr", "owner": "SIX"},
            {"stage": "Tradeweb contracted share", "value": round(tradeweb, 1), "unit": "$mm/yr", "owner": "Tradeweb"},
        ],
    }
