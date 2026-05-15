from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history
from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
)
from app.db.prices import (
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=1)

CohortKey = tuple[str, str]

_BENCHMARK_TICKER: dict[CohortKey, tuple[str, str]] = {
    ("USD", "equity_etf"): ("^GSPC", "US"),
    ("INR", "equity_etf"): ("^NSEI", "IN"),
    ("USD", "crypto"): ("BTC-USD", "CRYPTO"),
    ("INR", "crypto"): ("BTC-USD", "CRYPTO"),
}


def _key(ticker: str) -> str:
    return f"bench:{ticker}:1y"


async def get_benchmark_history(cohort: CohortKey) -> list[tuple[date, float]]:
    """Return a 1y daily-close series for the cohort's benchmark. Empty list on failure."""
    ticker_market = _BENCHMARK_TICKER.get(cohort)
    if ticker_market is None:
        return []
    ticker, market = ticker_market
    rows = await _fetch_cache_rows([_key(ticker)])
    if rows:
        raw: Any = rows[0]["value"]
        return [(date.fromisoformat(r["date"]), float(r["close"])) for r in raw]

    try:
        bars = await _fetch_price_history(ticker, period="1y", interval="1d", market=market)
    except Exception as e:
        logger.warning("benchmark fetch failed for %s — %s", ticker, e)
        return []

    series = [(b.date, float(b.close)) for b in bars]
    if not series:
        return []
    payload = [{"date": d.isoformat(), "close": v} for d, v in series]
    expires = datetime.now(UTC) + _TTL
    try:
        await _write_cache_rows([(_key(ticker), payload, expires)])  # type: ignore[list-item]
    except Exception as e:
        logger.warning("benchmark cache write failed: %s (not fatal)", e)
    return series
