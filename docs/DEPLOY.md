# Deploying six-tradeweb to Render.com

The repo ships a Render **Blueprint** (`render.yaml`) that provisions everything in
one step: a managed Postgres database and the FastAPI web service. No code changes
are needed — the app reads `DATABASE_URL` from the environment and coerces the
platform's `postgresql://` string to the async `asyncpg` driver itself.

## One-time deploy

1. **Push to Git** (GitHub / GitLab / Bitbucket). `render.yaml` must be at the
   **repo root**. If the app lives in a subfolder, uncomment the `rootDir:` lines
   in `render.yaml`.
2. **Render Dashboard → Blueprints → New Blueprint Instance →** connect the repo
   **→ Apply.** Render creates `six-tradeweb-db` (Postgres, Frankfurt) and the
   `six-tradeweb` web service. No credit card required for the free tier.
3. Wait for the build (`pip install -r requirements.txt`) and first boot
   (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
4. Open the `https://six-tradeweb.onrender.com` URL: check `/health`, the API docs
   at `/docs`, and the liquidity-graph page at `/ui/network`.

No manual seed step: `BOOTSTRAP_ON_STARTUP=true` makes the app create tables, seed
reference data, and refresh the Ai-Price universe on first boot, so it comes up
demo-ready.

## Environment variables (set by the blueprint)

| Var | Value | Why |
|-----|-------|-----|
| `DATABASE_URL` | from `six-tradeweb-db` | Postgres DSN; app adds the asyncpg driver |
| `TRADEWEB_USE_MOCK` | `true` | serve synthetic Tradeweb data (no credentials) |
| `BOOTSTRAP_ON_STARTUP` | `true` | seed + refresh on first boot |
| `PYTHON_VERSION` | `3.13.1` | if the build can't find it, use `3.12.x` |

To price against the real Tradeweb feed instead of the mock, set
`TRADEWEB_USE_MOCK=false` and add `TRADEWEB_API_KEY` (and `TRADEWEB_BASE_URL` if
different) in the service's **Environment** settings.

## Free-tier caveats

- **Postgres expires 30 days after creation** (1 GB, no backups, one free DB per
  workspace). Render emails warnings before deletion. For anything you need to keep,
  upgrade the database to **Basic (~$6/mo)**, or point `DATABASE_URL` at a
  non-expiring free Postgres (e.g. Neon) — any managed connection string works
  unchanged; just remove the `databases:` block from `render.yaml`.
- **Web service sleeps after 15 min idle** (30–60s cold start; 750 free
  instance-hours/month/workspace). For a live demo, hit the URL ~1 min beforehand
  to warm it, or run the service on Basic to remove the sleep.
- `/ai-price/refresh` **appends** a price snapshot each run (it builds history), and
  bootstrap runs on every cold-start boot. Harmless — the app always reads the
  latest snapshot per bond — but the snapshot table grows on the free tier's
  frequent restarts. To stop that after the first deploy, set
  `BOOTSTRAP_ON_STARTUP=false` (seeded data persists in Postgres regardless).

## Manual alternative (no blueprint)

1. **New → PostgreSQL** (free) → copy its **Internal Database URL**.
2. **New → Web Service** → connect the repo →
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/health`
   - Env vars: `DATABASE_URL` (the internal URL), `TRADEWEB_USE_MOCK=true`,
     `BOOTSTRAP_ON_STARTUP=true`.

## Resetting the database

The app creates tables on startup but never drops them. For a clean slate:

```
psql "$DATABASE_URL" -c "drop schema public cascade; create schema public;"
```

then restart the service (it recreates and re-seeds the schema).
