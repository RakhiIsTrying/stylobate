from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.data.yfinance_adapter import fetch_ticker_info as _fetch_ticker_info
from app.db.prices import (
    delete_cache_keys as _delete_cache_keys,
)
from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
)
from app.db.prices import (
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)
_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class Price:
    ticker: str
    market: str
    price: float
    currency: str
    as_of: datetime


def _key(ticker: str, market: str) -> str:
    return f"price:{ticker}:{market}"


async def get_prices(tickers: list[tuple[str, str]]) -> dict[tuple[str, str], Price]:
    """Return {(ticker, market): Price} for all hits + freshly-fetched misses.
    Tickers whose yfinance call fails are omitted from the response."""
    if not tickers:
        return {}
    keys = [_key(t, m) for t, m in tickers]
    rows = await _fetch_cache_rows(keys)
    hits: dict[str, dict[str, Any]] = {r["key"]: r["value"] for r in rows}
    out: dict[tuple[str, str], Price] = {}
    misses: list[tuple[str, str]] = []
    for ticker, market in tickers:
        if _key(ticker, market) in hits:
            v = hits[_key(ticker, market)]
            out[(ticker, market)] = Price(
                ticker=ticker, market=market,
                price=float(v["price"]), currency=v["currency"],
                as_of=datetime.fromisoformat(v["as_of"]),
            )
        else:
            misses.append((ticker, market))

    if not misses:
        return out

    fetched = await asyncio.gather(
        *[_fetch_ticker_info(t, market=m) for t, m in misses],
        return_exceptions=True,
    )
    now = datetime.now(UTC)
    expires = now + _TTL
    write_rows: list[tuple[str, dict[str, Any], datetime]] = []
    for (ticker, market), result in zip(misses, fetched, strict=True):
        if isinstance(result, BaseException):
            logger.warning("price fetch failed for %s:%s — %s", ticker, market, result)
            continue
        info: Any = result
        if info.last_price is None:
            continue
        out[(ticker, market)] = Price(
            ticker=ticker, market=market,
            price=float(info.last_price), currency=info.currency,
            as_of=now,
        )
        write_rows.append((
            _key(ticker, market),
            {"price": float(info.last_price), "currency": info.currency, "as_of": now.isoformat()},
            expires,
        ))
    if write_rows:
        try:
            await _write_cache_rows(write_rows)
        except Exception as e:
            logger.warning("cache_kv write failed: %s (not fatal)", e)
    return out


async def invalidate_prices(tickers: list[tuple[str, str]]) -> None:
    keys = [_key(t, m) for t, m in tickers]
    await _delete_cache_keys(keys)
