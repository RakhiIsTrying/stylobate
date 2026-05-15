from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import TickerInfo


@pytest.mark.asyncio
async def test_get_usdinr_fresh_fetch() -> None:
    fake = TickerInfo(ticker="USDINR=X", name="USD/INR", currency="USD",
                      market_cap=None, last_price=83.50)
    fetch_mock = AsyncMock(return_value=fake)
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate == 83.50
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_usdinr_cache_hit() -> None:
    now = datetime.now()
    rows = [{
        "key": "fx:USDINR",
        "value": {"rate": 83.42, "as_of": now.isoformat()},
        "expires_at": now + timedelta(minutes=30),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate == 83.42
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_usdinr_returns_none_on_error() -> None:
    fetch_mock = AsyncMock(side_effect=RuntimeError("yfinance down"))
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate is None
