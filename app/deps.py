"""Shared FastAPI dependencies (test-overridable)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from .clients import DealerwebClient, TradewebClient
from .config import get_settings


async def get_tradeweb_client() -> AsyncGenerator[TradewebClient, None]:
    client = TradewebClient(get_settings())
    try:
        yield client
    finally:
        await client.aclose()


async def get_dealerweb_client() -> AsyncGenerator[DealerwebClient, None]:
    client = DealerwebClient(get_settings())
    try:
        yield client
    finally:
        await client.aclose()


async def get_rates_client():
    from .clients.rates import RatesClient
    client = RatesClient(get_settings())
    try:
        yield client
    finally:
        await client.aclose()
