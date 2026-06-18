"""Deeper validation for inbound Ai-Price feed records.

Pydantic already enforces types and field bounds; this adds the business rules a
real ingestion/validation service applies before a record is allowed to price:
price coherence, sane ranges, date sanity, and ISIN integrity. Duplicate
detection needs the database and lives in the ingest router.

`validate_feed_record` is pure and returns hard ``violations`` (block, 422) and
soft ``warnings`` (accept, but flag).
"""
from __future__ import annotations

import datetime as dt


def _isin_format_ok(isin: str) -> bool:
    return (len(isin) == 12 and isin[:2].isalpha() and isin[-1].isdigit()
            and isin.isalnum())


def _isin_check_digit_ok(isin: str) -> bool:
    """Luhn mod-10 over the digit-expanded alphanumeric ISIN body."""
    try:
        digits = "".join(str(int(ch, 36)) for ch in isin)
    except ValueError:
        return False
    total, dbl = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if dbl:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        dbl = not dbl
    return total % 10 == 0


def validate_feed_record(rec, *, today: dt.date | None = None) -> dict[str, list[str]]:
    today = today or dt.date.today()
    violations: list[str] = []
    warnings: list[str] = []

    if not (rec.ai_price_bid <= rec.ai_price_mid <= rec.ai_price_ask):
        violations.append("price incoherent: require bid <= mid <= ask")
    if not (1.0 <= rec.ai_price_mid <= 250.0):
        violations.append("mid price outside sane bond range [1, 250]")
    if rec.maturity_date <= rec.valuation_date:
        violations.append("maturity_date must be after valuation_date")
    if rec.valuation_date > today + dt.timedelta(days=1):
        violations.append("valuation_date is in the future")
    if rec.pricing_timestamp.date() > today + dt.timedelta(days=1):
        violations.append("pricing_timestamp is in the future")

    if rec.isin:
        if not _isin_format_ok(rec.isin):
            violations.append("ISIN format invalid (expect 12 chars: 2 alpha + 9 alnum + check digit)")
        elif not _isin_check_digit_ok(rec.isin):
            warnings.append("ISIN check digit mismatch")

    if rec.days_since_trade is not None and rec.days_since_trade > 90:
        warnings.append("stale: no trade in over 90 days")

    return {"violations": violations, "warnings": warnings}
