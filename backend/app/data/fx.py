from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.data.yfinance_adapter import fetch_ticker_info as _fetch_ticker_info
from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
)
from app.db.prices import (
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=1)
_KEY = "fx:USDINR"


async def get_usdinr() -> float | None:
    """Live USDINR rate. 1h cache. None on fetch failure."""
    rows = await _fetch_cache_rows([_KEY])
    if rows:
        return float(rows[0]["value"]["rate"])

    try:
        info = await _fetch_ticker_info("USDINR=X", market="US")
    except Exception as e:
        logger.warning("USDINR fetch failed: %s", e)
        return None
    if info.last_price is None:
        return None
    rate = float(info.last_price)
    now = datetime.now(UTC)
    try:
        await _write_cache_rows([
            (_KEY, {"rate": rate, "as_of": now.isoformat()}, now + _TTL)
        ])
    except Exception as e:
        logger.warning("FX cache write failed: %s (not fatal)", e)
    return rate
