"""Tradeweb market-data client (rich Municipal Ai-Price feed).

Accepts an injectable ``httpx`` transport for deterministic tests and ships a
synthetic feed so the full stack runs with no credentials. The synthetic feed
produces an internally-consistent rich record per bond: evaluated bid/mid/ask,
curve spreads, yield-to-worst/call, duration/convexity/DV01, rating, sector,
liquidity and a model-confidence band.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass

import httpx

from ..config import Settings


@dataclass(slots=True)
class AiPriceRecord:
    cusip: str
    description: str
    state: str
    sector: str
    rating_sp: str
    coupon: float
    maturity: dt.date
    callable: bool
    call_date: dt.date | None
    size_outstanding_mm: float
    price_type: str
    eval_bid: float
    ai_price: float
    eval_ask: float
    price_change_1d: float
    ai_yield: float
    yield_to_worst: float
    yield_to_call: float | None
    benchmark_spread_bp: float
    ust_spread_bp: float
    effective_duration: float
    convexity: float
    dv01: float
    liquidity_score: float
    trades_30d: int
    last_trade_date: dt.date | None
    confidence: float
    model_version: str
    as_of: dt.datetime


@dataclass(slots=True)
class FiQuoteRecord:
    isin: str
    clean_price: float
    yield_to_maturity: float
    as_of: dt.datetime


class TradewebError(RuntimeError):
    pass


# (cusip, description, state, coupon, maturity, sector, rating, callable, call_date, size_mm)
_MUNI_UNIVERSE = [
    ("13063DAB7", "California St GO", "CA", 5.000, dt.date(2032, 9, 1), "GO", "AA-", True, dt.date(2030, 9, 1), 500),
    ("64966QCJ9", "New York City NY GO", "NY", 4.000, dt.date(2030, 8, 1), "GO", "AA", True, dt.date(2028, 8, 1), 350),
    ("882723YK4", "Texas St Wtr Dev Rev", "TX", 3.250, dt.date(2035, 4, 1), "REVENUE", "AAA", True, dt.date(2032, 4, 1), 200),
    ("452152AR7", "Illinois St GO", "IL", 5.500, dt.date(2029, 5, 1), "GO", "A-", False, None, 750),
    ("344631AC2", "Florida St Brd Ed Rev", "FL", 4.250, dt.date(2033, 6, 1), "REVENUE", "AA+", True, dt.date(2031, 6, 1), 300),
    ("574193QH9", "Maryland St GO", "MD", 3.000, dt.date(2031, 3, 15), "GO", "AAA", False, None, 250),
    ("79770GGV8", "San Francisco CA Util Rev", "CA", 5.000, dt.date(2034, 7, 1), "REVENUE", "AA", True, dt.date(2031, 7, 1), 180),
    ("13066YTY5", "California Health Fac Rev", "CA", 4.000, dt.date(2036, 11, 1), "REVENUE", "A", True, dt.date(2033, 11, 1), 220),
    ("64971XAN0", "New York Dorm Auth Rev", "NY", 5.250, dt.date(2038, 3, 15), "REVENUE", "AA-", True, dt.date(2034, 3, 15), 400),
    ("882724AB3", "Texas A&M Univ Rev", "TX", 3.500, dt.date(2032, 5, 15), "REVENUE", "AAA", True, dt.date(2030, 5, 15), 150),
    ("452200CD1", "Chicago IL GO", "IL", 5.750, dt.date(2037, 1, 1), "GO", "BBB+", True, dt.date(2032, 1, 1), 600),
    ("574200EF2", "Maryland Trans Auth Rev", "MD", 4.000, dt.date(2034, 7, 1), "REVENUE", "AA", True, dt.date(2031, 7, 1), 280),
]

# Indicative AAA-muni credit spread add-on by rating (bp), revenue adds a touch.
_RATING_SPREAD_BP = {
    "AAA": 5, "AA+": 12, "AA": 20, "AA-": 28,
    "A+": 40, "A": 55, "A-": 70, "BBB+": 110, "BBB": 150,
}


def _seeded_rng(*parts: object) -> random.Random:
    key = "|".join(str(p) for p in parts)
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def _benchmark_yield(years: float) -> float:
    """Toy upward-sloping AAA-muni benchmark curve (percent)."""
    return round(2.55 + 0.045 * min(years, 30), 4)


def _build_record(row, now: dt.datetime, intraday: bool) -> AiPriceRecord:
    cusip, desc, state, coupon, maturity, sector, rating, callable_, call_date, size = row
    day = now.date().isoformat()
    rng = _seeded_rng(cusip, day, "intraday" if intraday else "eod")

    years = max((maturity - now.date()).days / 365.25, 0.25)
    bench = _benchmark_yield(years)

    # Approximate effective duration (needed for the curve term below).
    duration = round(min(years * 0.82, 13.0) * (1 - 0.02 * (coupon - 4)), 3)
    duration = max(duration, 0.5)

    # Structural spread: rating + sector + a curve/duration term.
    spread_bp = _RATING_SPREAD_BP.get(rating, 60)
    spread_bp += 8 if sector == "REVENUE" else 0
    spread_bp += 2.0 * duration
    # Liquidity-driven richness/cheapness -- the inefficiency the RV model
    # surfaces: small issues trade cheap (wider), benchmark-size issues rich.
    liq_adj = 25.0 * max(0.0, (400 - size) / 400) - 15.0 * max(0.0, (size - 500) / 250)
    spread_bp += liq_adj + rng.uniform(-4, 4)
    if intraday:
        spread_bp += rng.uniform(-3, 3)

    ai_yield = round(bench + spread_bp / 100.0, 4)
    ai_price = round(100 - (ai_yield - coupon) * duration, 4)
    half_spread = round((spread_bp / 100.0) * duration * 0.06 + rng.uniform(0.02, 0.12), 4)
    eval_bid = round(ai_price - half_spread, 4)
    eval_ask = round(ai_price + half_spread, 4)

    convexity = round(duration**2 / 95.0, 4)
    dv01 = round(duration * ai_price * 0.0001, 6)  # price pts / bp / 100 face

    # yield-to-call / worst for callable bonds
    if callable_ and call_date and call_date > now.date():
        ytc = round(ai_yield - rng.uniform(0.05, 0.35), 4)
        ytw = round(min(ai_yield, ytc), 4)
    else:
        ytc = None
        ytw = ai_yield

    # liquidity + confidence: better for higher-rated, larger, more-traded issues
    base_liq = 40 + (size / 750) * 35 + (1 if rating.startswith("AAA") else 0) * 15
    trades_30d = max(0, int(rng.gauss(base_liq / 8, 3)))
    liquidity_score = round(min(99.0, base_liq + trades_30d * 1.5 + rng.uniform(-5, 5)), 1)
    if trades_30d > 0:
        last_trade = now.date() - dt.timedelta(days=rng.randint(0, 29))
    else:
        last_trade = now.date() - dt.timedelta(days=rng.randint(45, 240))
    confidence = round(min(0.99, 0.70 + liquidity_score / 400 + (0.05 if not callable_ else 0)), 3)

    # muni/UST relationship: pre-tax muni yields sit below UST (tax exemption)
    ust_ratio = rng.uniform(0.72, 0.88)
    ust_yield = ai_yield / ust_ratio
    ust_spread_bp = round((ai_yield - ust_yield) * 100, 1)  # negative pre-tax

    # Daily move tracks the SIX risk-free curve, scaled by how liquid the eval is:
    # liquid evals track the curve (beta ~ 1), illiquid ones lag and go stale.
    from ..analytics.freshness import daily_move_bp, responsiveness
    _expected = -duration * (daily_move_bp(years) / 10000.0) * ai_price
    price_change_1d = round(responsiveness(liquidity_score) * _expected + rng.uniform(-0.01, 0.01), 4)

    return AiPriceRecord(
        cusip=cusip, description=desc, state=state, sector=sector, rating_sp=rating,
        coupon=coupon, maturity=maturity, callable=callable_, call_date=call_date,
        size_outstanding_mm=float(size),
        price_type="INTRADAY" if intraday else "EOD",
        eval_bid=eval_bid, ai_price=ai_price, eval_ask=eval_ask,
        price_change_1d=price_change_1d,
        ai_yield=ai_yield, yield_to_worst=ytw, yield_to_call=ytc,
        benchmark_spread_bp=round(spread_bp, 1), ust_spread_bp=ust_spread_bp,
        effective_duration=duration, convexity=convexity, dv01=dv01,
        liquidity_score=liquidity_score, trades_30d=trades_30d,
        last_trade_date=last_trade, confidence=confidence,
        model_version="aiprice-2.4-mock", as_of=now,
    )


class TradewebClient:
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

    async def __aenter__(self) -> "TradewebClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def fetch_ai_price(
        self, *, state: str | None = None, intraday: bool = False
    ) -> list[AiPriceRecord]:
        if self._settings.tradeweb_use_mock:
            now = dt.datetime.now(dt.timezone.utc)
            rows = [r for r in _MUNI_UNIVERSE if not state or r[2] == state.upper()]
            return [_build_record(r, now, intraday) for r in rows]

        try:
            params = {"intraday": str(intraday).lower()}
            if state:
                params["state"] = state
            resp = await self._client.get("/municipal/ai-price", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TradewebError(f"Ai-Price fetch failed: {exc}") from exc
        return [self._parse_ai_price(row) for row in resp.json().get("data", [])]

    async def fetch_fi_quotes(self, isins: list[str]) -> list[FiQuoteRecord]:
        if self._settings.tradeweb_use_mock:
            now = dt.datetime.now(dt.timezone.utc)
            bucket = now.replace(second=0, microsecond=0)
            out = []
            for isin in isins:
                rng = _seeded_rng(isin, bucket.isoformat())
                out.append(FiQuoteRecord(isin, round(rng.uniform(95, 105), 4),
                                         round(rng.uniform(0.5, 4.5), 4), now))
            return out
        try:
            resp = await self._client.post("/fixed-income/quotes", json={"isins": isins})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TradewebError(f"FI quote fetch failed: {exc}") from exc
        return [
            FiQuoteRecord(r["isin"], float(r["cleanPrice"]), float(r["ytm"]),
                          dt.datetime.fromisoformat(r["asOf"]))
            for r in resp.json().get("data", [])
        ]

    @staticmethod
    def _parse_ai_price(row: dict) -> AiPriceRecord:
        def d(k):
            return dt.date.fromisoformat(row[k]) if row.get(k) else None
        return AiPriceRecord(
            cusip=row["cusip"], description=row["description"], state=row["state"],
            sector=row["sector"], rating_sp=row["rating"], coupon=float(row["coupon"]),
            maturity=dt.date.fromisoformat(row["maturity"]),
            callable=bool(row.get("callable")), call_date=d("callDate"),
            size_outstanding_mm=float(row["sizeOutstandingMM"]),
            price_type=row.get("priceType", "EOD"),
            eval_bid=float(row["evalBid"]), ai_price=float(row["aiPrice"]),
            eval_ask=float(row["evalAsk"]), price_change_1d=float(row.get("priceChange1d", 0)),
            ai_yield=float(row["aiYield"]), yield_to_worst=float(row["yieldToWorst"]),
            yield_to_call=(float(row["yieldToCall"]) if row.get("yieldToCall") else None),
            benchmark_spread_bp=float(row["benchmarkSpreadBp"]),
            ust_spread_bp=float(row["ustSpreadBp"]),
            effective_duration=float(row["effectiveDuration"]),
            convexity=float(row["convexity"]), dv01=float(row["dv01"]),
            liquidity_score=float(row["liquidityScore"]),
            trades_30d=int(row.get("trades30d", 0)), last_trade_date=d("lastTradeDate"),
            confidence=float(row["confidence"]),
            model_version=row.get("modelVersion", "aiprice-2.x"),
            as_of=dt.datetime.fromisoformat(row["asOf"]),
        )
