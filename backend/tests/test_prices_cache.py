from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import TickerInfo


@pytest.mark.asyncio
async def test_get_prices_all_hits() -> None:
    now = datetime.now()
    rows = [
        {"key": "price:AAPL:US",
         "value": {"price": 189.42, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
        {"key": "price:BTC:CRYPTO",
         "value": {"price": 80652.0, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
    ]
    fetch_mock = AsyncMock()
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_prices_all_misses_fetches_and_writes() -> None:
    fetch_mock = AsyncMock(side_effect=[
        TickerInfo(ticker="AAPL", name="Apple Inc.", currency="USD",
                   market_cap=None, last_price=189.42),
        TickerInfo(ticker="BTC", name="Bitcoin", currency="USD",
                   market_cap=None, last_price=80652.0),
    ])
    write_mock = AsyncMock()
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", write_mock):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    assert fetch_mock.await_count == 2
    write_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_prices_mixed_hits_and_misses() -> None:
    now = datetime.now()
    rows = [
        {"key": "price:AAPL:US",
         "value": {"price": 189.42, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
    ]
    fetch_mock = AsyncMock(return_value=TickerInfo(
        ticker="BTC", name="Bitcoin", currency="USD",
        market_cap=None, last_price=80652.0,
    ))
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    assert fetch_mock.await_count == 1
    fetch_mock.assert_awaited_with("BTC", market="CRYPTO")


@pytest.mark.asyncio
async def test_get_prices_yfinance_error_returns_partial() -> None:
    """When yfinance fails for one ticker, others still come back."""
    fetch_mock = AsyncMock(side_effect=[
        TickerInfo(ticker="AAPL", name="Apple", currency="USD",
                   market_cap=None, last_price=189.42),
        RuntimeError("yfinance rate-limited"),
    ])
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert ("AAPL", "US") in out
    assert ("BTC", "CRYPTO") not in out


@pytest.mark.asyncio
async def test_invalidate_prices_deletes_keys() -> None:
    delete_mock = AsyncMock()
    with patch("app.data.prices_cache._delete_cache_keys", delete_mock):
        from app.data.prices_cache import invalidate_prices
        await invalidate_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    delete_mock.assert_awaited_once()
    call = delete_mock.await_args
    assert call is not None
    keys = call.args[0]
    assert "price:AAPL:US" in keys
    assert "price:BTC:CRYPTO" in keys
