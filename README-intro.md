# SIX × Tradeweb — Data Distribution Service

A FastAPI + PostgreSQL service with an HTML console, modelling how **SIX**
consumes **Tradeweb** products. The architecture reflects the *actual*
relationship between the two firms rather than a fictional one:

- **SIX runs its own exchange.** Its listings (equities, ETFs, listed bonds)
  are SIX's own business — exposed here under `/instruments`.
- **SIX is a Tradeweb data/pricing distribution partner.** Tradeweb-sourced
  evaluated fixed-income prices live under `/pricing`.
- **Tradeweb Municipal Ai-Price** — the ML-driven U.S. municipal bond pricing
  service SIX has offered its clients since 2022 — gets its **own dedicated
  route** under `/ai-price`, because it is a separately-licensed product with
  its own data shape (CUSIP-keyed, model confidence, model version).
- **Analytics layer.** The Ai-Price feed carries a rich evaluated record
  (bid/mid/ask, curve & UST spreads, yield-to-worst/call, duration,
  convexity, DV01, rating, sector, call schedule, liquidity, trade count,
  model confidence). On top of it the service computes tax-equivalent
  yield, a relative-value rich/cheap screen, a market summary, and
  portfolio valuation + risk — the value SIX can monetise from the feed.
- **Dealerweb (prospective).** Inter-dealer UST / TBA-MBS top-of-book and
  liquidity analytics under `/dealerweb`. SIX does **not** distribute
  Dealerweb today; it is modelled as a clearly-labelled prospective
  product (UI badge + docstrings).

There is deliberately **no** "SIX exchange runs on Tradeweb/Dealerweb" coupling,
because no such relationship exists publicly.

## Layout

```
app/
  config.py            settings (env-driven)
  db.py                async engine + session
  models.py            Instrument, FixedIncomeQuote, AiPriceQuote,
                       DealerwebQuote, Portfolio, Holding, UsageEvent,
                       PriceChallenge, ModelAdjustment
  schemas.py           Pydantic v2 I/O models
  deps.py              Tradeweb client dependency (test-overridable)
  seed.py              reference SIX instruments
  clients/tradeweb.py  Tradeweb client: rich Ai-Price feed (injectable transport)
  clients/dealerweb.py Dealerweb inter-dealer client (UST + TBA-MBS)
  analytics/muni.py    TEY, relative value, market summary
  analytics/portfolio.py  valuation + MV-weighted risk
  analytics/liquidity.py  z-score, drift, regime, sector stress (Model B)
  clients/history.py      synthetic spread-history for the signal engine
  clients/rates.py        SIX rates feed: SARON / CHF curve + USD curve
  analytics/enrichment.py identity (ISIN), curve anchor, bundle assembly
  analytics/freshness.py  curve-tracking responsiveness (headline signal)
  analytics/consensus.py  Ai-Price vs blend of bank-client marks
  clients/contributors.py synthetic multi-source contributor marks
  routers/
    health.py          /health, /ready
    instruments.py     /instruments        (SIX listings)
    pricing.py         /pricing            (Tradeweb FI evaluated prices)
    ai_price.py        /ai-price           (Ai-Price data + analytics)
    dealerweb.py       /dealerweb          (inter-dealer, prospective)
    portfolios.py      /portfolios         (valuation + risk)
    liquidity.py       /liquidity          (liquidity intelligence / GPS)
    enrichment.py      /rates, /enriched   (SIX bundled product)
    feedback.py        /feedback           (freshness, model review, demand
                                           momentum, validation bias, consensus)
    consensus.py       /consensus          (Ai-Price vs bank-client marks)
    flywheel.py        /challenges, /flywheel (closed-loop feedback)
  templates/index.html Swiss-modernist console
tests/                 46 tests (+ flywheel: consume/challenge/retrain)
docker-compose.yml     local Postgres 16
```

## Run it (Postgres)

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # TRADEWEB_USE_MOCK=true by default

docker compose up -d db              # Postgres on :5432
python -m app.seed                   # create tables + seed instruments
uvicorn app.main:app --reload        # http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000/` for the console, or `/docs` for the OpenAPI UI.

### Run with no Postgres (SQLite, e.g. for a quick look)

```bash
export DATABASE_URL="sqlite+aiosqlite:///./run.db"
python -m app.seed
uvicorn app.main:app
```

## The Tradeweb feed

`TRADEWEB_USE_MOCK=true` (the default) serves a **deterministic synthetic
feed** so the whole stack runs with no credentials. The mock is keyed on
CUSIP + the current minute, so values are stable within a minute and move
between minutes — handy for demoing the Ai-Price history/charting route.

To point at a live Tradeweb API, set `TRADEWEB_USE_MOCK=false`,
`TRADEWEB_BASE_URL`, and `TRADEWEB_API_KEY`. The client (`clients/tradeweb.py`)
maps the live JSON shape and raises `TradewebError` on transport/HTTP failures,
which the routers surface as `502`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/instruments` | SIX listings (filter `?asset_class=equity\|etf\|bond`) |
| POST | `/instruments` | Add a listing |
| GET | `/instruments/{isin}` | One listing |
| POST | `/pricing/refresh` | Pull Tradeweb FI prices for listed bonds |
| GET | `/pricing/{isin}` | Stored quote history for a bond |
| POST | `/ai-price/refresh` | Ingest latest Ai-Price snapshot (`?state=CA`) |
| GET | `/ai-price/latest` | Latest per CUSIP (`?state=`, `?min_confidence=`) |
| GET | `/ai-price/{cusip}/history` | Ai-Price time series for one CUSIP |
| GET | `/health`, `/ready` | Liveness; readiness checks the DB |

## Tests

```bash
pytest            # 13 passing — uses SQLite + httpx.MockTransport, no Postgres
```

The suite covers the API surface, the dedicated Ai-Price route (refresh, latest,
state/confidence filters, history 404), and the **live** client branch via an
injected `httpx.MockTransport`, including error mapping to `TradewebError`.

## Notes / honest gaps

- Tables are auto-created on startup for dev convenience. For production, add
  **Alembic** migrations and drop the `init_models()` startup call.
- The Tradeweb data shapes in the live branch are illustrative; confirm field
  names against the actual Tradeweb data API contract before going live.
- The synthetic feed is for demonstration only — not investment advice.

## Analytics endpoints

Municipal Ai-Price (`/ai-price`):

- `POST /ai-price/refresh?state=&intraday=` — ingest a snapshot (EOD or intraday).
- `GET  /ai-price/latest?state=&min_confidence=` — latest rich record per CUSIP.
- `GET  /ai-price/analytics/summary?marginal_rate=&state=` — market-level stats
  (avg yield, avg tax-equivalent yield, avg spread/duration, illiquid %, breakdowns).
- `GET  /ai-price/analytics/relative-value?signal=cheap|fair|rich` — rich/cheap
  screen: actual curve spread vs an explainable expected spread, with residual,
  percentile and signal.
- `GET  /ai-price/{cusip}/tax-equivalent?marginal_rate=` — TEY + yield pickup.
- `GET  /ai-price/{cusip}/history?since=` — per-CUSIP price history.

Portfolios (`/portfolios`):

- `GET/POST /portfolios` — list / create (name + holdings of CUSIP + par).
- `GET /portfolios/{id}/valuation?marginal_rate=` — market value, MV-weighted
  duration / yield / TEY / confidence, summed DV01, sector & rating weights,
  and any CUSIPs with no current mark.

Liquidity intelligence (`/liquidity`, Model B proxy):

- `GET /liquidity/stress` — cross-sector "Liquidity GPS": per sector (UST,
  Agency MBS, Muni GO, Muni Revenue) a dislocation z-score, drift, stretch,
  trend, risk tier, regime and a 90-day bid/ask series, plus an overall
  stress score and a one-line interpretation.
- `GET /liquidity/signals/{instrument}` — per-instrument z-score, drift,
  regime, stress and the underlying series (CUSIP or Dealerweb instrument).

  Signals run on the top-of-book bid/ask spread series. History is synthetic
  (`clients/history.py`); the signal math is real and explainable. This is the
  SIX-native *proxy* tier — flow-derived signals (fill probability, dealer
  imbalance) would require a Dealerweb data partnership and are not claimed.

Dealerweb (`/dealerweb`, prospective):

- `POST /dealerweb/refresh?product=UST|TBA_MBS` — ingest inter-dealer top-of-book.
- `GET  /dealerweb/top-of-book?product=` — best bid/offer, size, spread, liquidity.
- `GET  /dealerweb/analytics/liquidity` — per-product spread/depth/liquidity summary.

## Enrichment / SIX bundle (`/rates`, `/enriched`)

Automates the SIX value-add: it joins a Tradeweb evaluated price to SIX's own
rates, identity and corporate-actions data and emits one analytics-ready record.

- `GET /rates/curve?currency=CHF|USD` — a SIX risk-free curve. CHF is anchored on
  SARON (the CHF overnight benchmark SIX administers); USD is a Treasury-style
  curve (the muni anchor).
- `GET /enriched/ai-price?marginal_rate=&state=` — the bundled municipal product:
  each Ai-Price record joined to a derived `ISIN` (real check digit), an issuer
  LEI, the call-adjusted yield, the risk-free anchor interpolated at the bond's
  tenor, spread-to-risk-free, tax-equivalent yield and TEY spread, with provenance.
- `GET /enriched/instruments` — SIX-listed CHF bonds anchored to the SARON curve
  (spread-to-benchmark), demonstrating the rates franchise on Swiss instruments.
- `GET /enriched/signals` — **the closed loop**: each muni is bundled, then the
  after-tax spread to the SIX risk-free curve becomes the series the dislocation
  z-score and drift run on, so SIX's own rates data (not just the raw credit
  spread) drives the intelligence. Returns per-CUSIP dislocation + relative-value
  signals and a sector roll-up; this is what the `Enriched bundle` panel renders.

The `Enriched bundle` panel in the dashboard renders the municipal product.

## Feedback to Tradeweb (`/feedback`)

Represents the SIX -> Tradeweb direction honestly. Client-facing analytics are
sold by SIX to its bank clients; the numbers that flow *back* to Tradeweb are
model-quality feedback.

- `GET /feedback/tradeweb?abs_z=&min_confidence=` — emits one **live** signal and
  marks three as pending:
    * `model_review_candidates` (live) — price-review/challenge list: a CUSIP is
      flagged when the Ai-Price confidence band is low or the dislocation z-score
      of its after-tax spread to the SIX risk-free curve is large.
    * `demand_signal` — requires request logging (per-CUSIP/sector query volume).
    * `data_quality_feedback` — requires multi-source ingest (golden-copy match
      rate, identifier conflicts, check-digit failures).
    * `consolidated_metrics` — prospective (cross-venue volume / fragmentation).

The `Model-quality feedback` panel in the dashboard renders this.

The headline feedback signal is **evaluation freshness**: per bond, `beta = actual price move / (-duration x curve_move x price)` against the SIX risk-free curve. Low beta on an illiquid bond = a stale mark a trader can't act on. The feed's daily move is curve-driven so this is recoverable. Demand and validation are refined to *momentum* and *signed bias* respectively.

## The flywheel (`/challenges`, `/flywheel`)

Closes the loop so client feedback measurably improves the next prices.

- Client consumption is logged (`UsageEvent`) whenever prices are fetched.
- `POST /challenges` — a client disputes an evaluated price (records the price
  they saw vs the level they argue).
- `POST /challenges/{id}/resolve` — SIX adjudicates; an **accept** writes a
  `ModelAdjustment` (settled - observed, in price points).
- `POST /ai-price/refresh` applies the summed adjustments to the fresh snapshot
  (price/bid/ask nudged, yield re-derived, confidence lifted) and tags the model
  version `...+fbN` -- so accepted feedback moves the next prices.
- `GET /flywheel` — loop counters (priced -> consumed -> challenged -> accepted ->
  adjustments) and the current model version.
- `POST /flywheel/simulate` — turns the loop once: challenges the weakest-confidence
  price, accepts a small correction, re-ingests, and reports the price move.

As a result, two `/feedback/tradeweb` signals are now **live** from real in-app
events -- `demand_signal` (from usage logs) and `validation_feedback` (from
challenges/adjustments) -- alongside `model_review_candidates`; only
`data_quality_feedback` and `consolidated_metrics` remain pending.

The `Flywheel` dashboard panel shows the live counters and a Turn-the-flywheel
button.

## Caveats & simplifications

- **Dealerweb is prospective**: SIX does not distribute it today. It is included
  to model the inter-dealer rates/MBS liquidity-intelligence value, clearly
  labelled throughout.
- **Tax-equivalent yield** uses a single combined marginal rate (default 0.37);
  in-state double tax-exemption is left to the caller to fold in.
- **Ai-Price universe is U.S. munis** — consistent with what SIX distributes.
  A Swiss-instrument extension would flow through `/pricing` on CHF bonds.
- **SARON anchors CHF, not US munis** — munis are spread against the USD curve;
  the SARON curve is used for the CHF SIX-listed bonds. The SIX rates levels here
  are synthetic placeholders.
- **Live client field names are illustrative** — confirm against the real
  Tradeweb / Dealerweb API contracts before pointing at production endpoints.
- **Schema auto-creates on startup**; use Alembic migrations for production.

## Docs

- `docs/METHODOLOGY.md` — every calculation, source and the end-to-end flow (with diagrams).
- `docs/value-exchange.html`, `docs/system-flow.html`, `docs/flywheel.html` — standalone HTML views (open in a browser).
