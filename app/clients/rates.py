"""SIX rates feed: benchmark reference-rate curves SIX administers/publishes.

SIX is the benchmark administrator of SARON (the CHF overnight reference rate
that replaced CHF LIBOR) and calculates the Swiss Reference Rates. Here we model
a SIX rates service that publishes a risk-free curve per currency:

  * CHF -- SARON at the front, extending out the Swiss-franc curve.
  * USD -- a Treasury-style risk-free curve (the anchor for US munis).

Synthetic but real-shaped, deterministic, with the same injectable-transport
pattern as the other clients. The curve is the risk-free anchor the enrichment
engine spreads instruments against.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx

from ..config import Settings


@dataclass(slots=True)
class CurvePoint:
    tenor: str
    years: float
    rate: float  # percent


@dataclass(slots=True)
class RatesSnapshot:
    currency: str
    benchmark: str          # front/overnight benchmark name
    overnight_rate: float   # e.g. SARON level for CHF
    points: list[CurvePoint]
    as_of: dt.datetime


class RatesError(RuntimeError):
    pass


# (tenor, years, rate%) -- synthetic, indicative shapes.
_CHF = [
    ("ON", 0.003, 0.55), ("3M", 0.25, 0.60), ("6M", 0.5, 0.66), ("12M", 1.0, 0.74),
    ("2Y", 2.0, 0.88), ("5Y", 5.0, 1.18), ("10Y", 10.0, 1.55), ("30Y", 30.0, 1.95),
]
_USD = [
    ("3M", 0.25, 4.30), ("6M", 0.5, 4.22), ("2Y", 2.0, 4.10), ("5Y", 5.0, 4.05),
    ("10Y", 10.0, 4.25), ("30Y", 30.0, 4.55),
]
_BENCHMARK = {"CHF": "SARON", "USD": "UST risk-free"}


class RatesClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.tradeweb_base_url, transport=transport, timeout=10.0
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RatesClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def fetch_curve(self, currency: str = "USD") -> RatesSnapshot:
        ccy = currency.upper()
        if self._settings.tradeweb_use_mock:
            raw = {"CHF": _CHF, "USD": _USD}.get(ccy)
            if raw is None:
                raise RatesError(f"No curve for {ccy}")
            pts = [CurvePoint(t, y, r) for t, y, r in raw]
            return RatesSnapshot(
                currency=ccy, benchmark=_BENCHMARK[ccy], overnight_rate=pts[0].rate,
                points=pts, as_of=dt.datetime.now(dt.timezone.utc),
            )
        try:
            resp = await self._client.get("/rates/curve", params={"ccy": ccy})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RatesError(f"Rates fetch failed: {exc}") from exc
        data = resp.json()
        pts = [CurvePoint(p["tenor"], float(p["years"]), float(p["rate"])) for p in data["points"]]
        return RatesSnapshot(
            currency=ccy, benchmark=data["benchmark"], overnight_rate=float(data["overnightRate"]),
            points=pts, as_of=dt.datetime.fromisoformat(data["asOf"]),
        )
