from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Bar


def _sample_bars(n: int = 250) -> list[Bar]:
    d0 = date(2025, 5, 15)
    return [
        Bar(date=(d0 + timedelta(days=i)), open=100.0, high=100.0, low=100.0,
            close=100.0 + i, volume=0)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_get_benchmark_history_returns_usd_equity_series() -> None:
    bars = _sample_bars(250)
    fetch_mock = AsyncMock(return_value=bars)
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert len(series) == 250
    assert series[0][0].toordinal() < series[-1][0].toordinal()
    fetch_mock.assert_awaited_with("^GSPC", period="1y", interval="1d", market="US")


@pytest.mark.asyncio
async def test_get_benchmark_history_cache_hit_skips_fetch() -> None:
    now = datetime.now()
    cached_value = [
        {"date": "2025-05-15", "close": 100.0},
        {"date": "2025-05-16", "close": 101.0},
    ]
    rows = [{
        "key": "bench:^GSPC:1y",
        "value": cached_value,
        "expires_at": now + timedelta(minutes=30),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert len(series) == 2
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_benchmark_history_nse_for_inr_equity() -> None:
    fetch_mock = AsyncMock(return_value=_sample_bars(10))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        await get_benchmark_history(("INR", "equity_etf"))
    fetch_mock.assert_awaited_with("^NSEI", period="1y", interval="1d", market="IN")


@pytest.mark.asyncio
async def test_get_benchmark_history_btc_for_usd_crypto() -> None:
    fetch_mock = AsyncMock(return_value=_sample_bars(10))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        await get_benchmark_history(("USD", "crypto"))
    fetch_mock.assert_awaited_with("BTC-USD", period="1y", interval="1d", market="CRYPTO")


@pytest.mark.asyncio
async def test_get_benchmark_history_returns_empty_on_fetch_error() -> None:
    fetch_mock = AsyncMock(side_effect=RuntimeError("yfinance down"))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert series == []
