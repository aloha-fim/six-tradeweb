"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .db import init_models
from .routers import (ai_price, consensus, dealerweb, enrichment, feedback,
                      flywheel, health, instruments, liquidity, portfolios, pricing)

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup for local/dev convenience. In production use
    # Alembic migrations instead.
    await init_models()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(instruments.router)
    app.include_router(pricing.router)
    app.include_router(ai_price.router)
    app.include_router(dealerweb.router)
    app.include_router(portfolios.router)
    app.include_router(liquidity.router)
    app.include_router(enrichment.router)
    app.include_router(feedback.router)
    app.include_router(flywheel.router)
    app.include_router(consensus.router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"service": settings.app_name}
        )

    return app


app = create_app()
