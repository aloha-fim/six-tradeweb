"""Dealerweb inter-dealer data client (UST + TBA-MBS top-of-book).

NOTE ON SCOPE: SIX does not distribute Dealerweb data today. This models a
*prospective* SIX data product: inter-dealer wholesale liquidity intelligence
(best bid/offer, size, spread) for U.S. rates and agency MBS, of the kind
Dealerweb is known for as the largest TBA-MBS inter-dealer broker.

Like the Tradeweb client it accepts an injectable transport and ships a
synthetic feed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass

import httpx

from ..config import Settings


@dataclass(slots=True)
class DealerwebRecord:
    product: str        # "UST" | "TBA_MBS"
    instrument: str
    tenor: str
    coupon: float | None
    bid: float
    ask: float
    mid: float
    bid_size_mm: float
    ask_size_mm: float
    spread_bp: float
    liquidity_score: float
    as_of: dt.datetime


class DealerwebError(RuntimeError):
    pass


# UST on-the-run / off-the-run points: (instrument, tenor, approx yield %)
_UST = [
    ("UST 2Y OTR", "2Y", 4.10),
    ("UST 5Y OTR", "5Y", 4.05),
    ("UST 10Y OTR", "10Y", 4.25),
    ("UST 30Y OTR", "30Y", 4.55),
]
# TBA-MBS coupons (FNCL 30Y): (instrument, tenor, coupon)
_MBS = [
    ("FNCL 5.0 30Y", "30Y", 5.0),
    ("FNCL 5.5 30Y", "30Y", 5.5),
    ("FNCL 6.0 30Y", "30Y", 6.0),
    ("GNMA 5.5 30Y", "30Y", 5.5),
]


def _rng(*parts: object) -> random.Random:
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def _ust_record(row, now) -> DealerwebRecord:
    instrument, tenor, base_yield = row
    rng = _rng(instrument, now.replace(second=0, microsecond=0).isoformat())
    # Price quoted in points of par around 100 (toy mapping from yield drift).
    mid = round(100 - (base_yield - 4.0) * 2 + rng.uniform(-0.20, 0.20), 4)
    half = round(rng.uniform(0.004, 0.020), 4)  # tight inter-dealer market
    bid, ask = round(mid - half, 4), round(mid + half, 4)
    spread_bp = round((ask - bid) / mid * 10000, 2)
    liq = round(min(99, 92 - {"2Y": 0, "5Y": 2, "10Y": 0, "30Y": 6}.get(tenor, 4)
                    + rng.uniform(-3, 3)), 1)
    return DealerwebRecord("UST", instrument, tenor, None, bid, ask, mid,
                           round(rng.uniform(25, 300), 1), round(rng.uniform(25, 300), 1),
                           spread_bp, liq, now)


def _mbs_record(row, now) -> DealerwebRecord:
    instrument, tenor, coupon = row
    rng = _rng(instrument, now.replace(second=0, microsecond=0).isoformat())
    mid = round(98 + (coupon - 5.0) * 1.6 + rng.uniform(-0.25, 0.25), 4)
    half = round(rng.uniform(0.015, 0.060), 4)  # wider than UST
    bid, ask = round(mid - half, 4), round(mid + half, 4)
    spread_bp = round((ask - bid) / mid * 10000, 2)
    liq = round(min(98, 78 + (coupon - 5.0) * 4 + rng.uniform(-5, 5)), 1)
    return DealerwebRecord("TBA_MBS", instrument, tenor, coupon, bid, ask, mid,
                           round(rng.uniform(50, 500), 1), round(rng.uniform(50, 500), 1),
                           spread_bp, liq, now)


class DealerwebClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        headers = {}
        if settings.tradeweb_api_key:
            headers["Authorization"] = f"Bearer {settings.tradeweb_api_key}"
        self._client = httpx.AsyncClient(
            base_url=settings.tradeweb_base_url, headers=headers,
            transport=transport, timeout=10.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DealerwebClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def fetch_top_of_book(self, product: str | None = None) -> list[DealerwebRecord]:
        if self._settings.tradeweb_use_mock:
            now = dt.datetime.now(dt.timezone.utc)
            out: list[DealerwebRecord] = []
            if product in (None, "UST"):
                out += [_ust_record(r, now) for r in _UST]
            if product in (None, "TBA_MBS"):
                out += [_mbs_record(r, now) for r in _MBS]
            return out
        try:
            params = {"product": product} if product else {}
            resp = await self._client.get("/wholesale/top-of-book", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DealerwebError(f"Dealerweb fetch failed: {exc}") from exc
        out = []
        for r in resp.json().get("data", []):
            out.append(DealerwebRecord(
                product=r["product"], instrument=r["instrument"], tenor=r["tenor"],
                coupon=(float(r["coupon"]) if r.get("coupon") is not None else None),
                bid=float(r["bid"]), ask=float(r["ask"]), mid=float(r["mid"]),
                bid_size_mm=float(r["bidSizeMM"]), ask_size_mm=float(r["askSizeMM"]),
                spread_bp=float(r["spreadBp"]), liquidity_score=float(r["liquidityScore"]),
                as_of=dt.datetime.fromisoformat(r["asOf"]),
            ))
        return out
