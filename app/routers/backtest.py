"""Backtesting endpoints: run a point-in-time backtest and summarize stored results."""
from __future__ import annotations

import math
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..backtest import run_backtest
from ..db import get_session
from ..models import BacktestResult
from ..routers.ai_price import _latest_rows
from ..routers.features import upsert_snapshot

router = APIRouter(tags=["Backtesting"])


@router.post("/backtest/run")
async def run(days: int = Query(default=10, ge=2, le=60),
              session: AsyncSession = Depends(get_session)) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    res = run_backtest(rows, days=days)
    snaps = 0
    for row in res["rows"]:
        if await upsert_snapshot(session, row["features"]):   # populate the feature history
            snaps += 1
        session.add(BacktestResult(
            run_id=res["run_id"], cusip=row["cusip"], as_of=row["as_of"], sector=row["sector"],
            predicted_price=row["predicted"], baseline_ai_price=row["baseline_ai"],
            actual_price=row["actual"], error_bp=row["error_bp"],
            abs_error_bp=row["abs_error_bp"], baseline_abs_error_bp=row["baseline_abs_error_bp"],
            model_version=res["model_version"]))
    await session.commit()
    return {"run_id": res["run_id"], "model_version": res["model_version"], "days": res["days"],
            "feature_snapshots_written": snaps,
            "method": ("point-in-time: predict each past date from info known then, compare to the "
                       "realized trade; baseline = raw Ai-Price. Leakage-free, versioned."),
            **res["summary"]}


@router.get("/backtest/results")
async def results(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.scalars(
        select(BacktestResult).order_by(BacktestResult.id.desc()).limit(2000))).all()
    if not rows:
        return {"n": 0, "note": "no backtest has been run yet"}
    err = [r.error_bp for r in rows]
    abs_err = [r.abs_error_bp for r in rows]
    base = [r.baseline_abs_error_bp for r in rows]
    mae, base_mae = mean(abs_err), mean(base)
    by_ver: dict[str, list[float]] = {}
    by_sec: dict[str, list[float]] = {}
    for r in rows:
        by_ver.setdefault(r.model_version, []).append(r.abs_error_bp)
        by_sec.setdefault(r.sector, []).append(r.abs_error_bp)
    return {
        "n": len(rows),
        "mae_bp": round(mae, 2),
        "rmse_bp": round(math.sqrt(mean([e * e for e in err])), 2),
        "bias_bp": round(mean(err), 2),
        "baseline_mae_bp": round(base_mae, 2),
        "improvement_pct": round(100 * (base_mae - mae) / base_mae, 1) if base_mae else 0.0,
        "by_model_version_mae_bp": {k: round(mean(v), 2) for k, v in by_ver.items()},
        "by_sector_mae_bp": {k: round(mean(v), 2) for k, v in by_sec.items()},
    }
