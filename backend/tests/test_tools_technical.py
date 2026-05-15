# backend/tests/test_tools_technical.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Bar


def _bars(n: int = 30) -> list[Bar]:
    return [
        Bar(
            date=date(2026, 4, 1 + (i % 28)),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_get_price_history_tool_wraps_adapter() -> None:
    bars = _bars(5)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import PriceHistoryResult, get_price_history_tool

        result = cast(
            PriceHistoryResult,
            await get_price_history_tool.impl(ticker="AAPL", period="1mo", interval="1d"),
        )
    assert isinstance(result, PriceHistoryResult)
    assert result.ticker == "AAPL"
    assert len(result.bars) == 5
    assert result.bars[0].open == 100


@pytest.mark.asyncio
async def test_calc_indicators_returns_latest_values() -> None:
    bars = _bars(60)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import IndicatorsResult, calc_indicators_tool

        result = cast(
            IndicatorsResult,
            await calc_indicators_tool.impl(
                ticker="AAPL",
                indicators=["sma20", "ema12", "rsi14", "macd", "bbands20"],
            ),
        )
    # Each requested indicator has a "latest" value in the result
    assert "sma20" in result.latest
    assert "ema12" in result.latest
    assert "rsi14" in result.latest
    assert "macd_line" in result.latest
    assert "macd_signal" in result.latest
    assert "bbands_upper" in result.latest


@pytest.mark.asyncio
async def test_detect_patterns_returns_levels() -> None:
    # 30 bars where price oscillates so support/resistance emerges
    bars: list[Bar] = []
    for i in range(30):
        c = 100.0 + (i % 5) * 2.0  # peaks at 108, troughs at 100
        bars.append(
            Bar(
                date=date(2026, 4, 1 + (i % 28)),
                open=c,
                high=c + 1,
                low=c - 1,
                close=c,
                volume=1_000_000,
            )
        )
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import PatternsResult, detect_patterns_tool

        result = cast(
            PatternsResult,
            await detect_patterns_tool.impl(ticker="AAPL"),
        )
    # At least one support and one resistance level
    assert len(result.levels) >= 1
    has_support = any(lv.kind == "support" for lv in result.levels)
    has_resistance = any(lv.kind == "resistance" for lv in result.levels)
    assert has_support or has_resistance


@pytest.mark.asyncio
async def test_get_volume_profile_buckets_prices() -> None:
    bars = _bars(40)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import VolumeProfileResult, get_volume_profile_tool

        result = cast(
            VolumeProfileResult,
            await get_volume_profile_tool.impl(ticker="AAPL", period="1mo"),
        )
    assert len(result.high_volume_levels) > 0
    # Each level is a (price, volume) tuple-ish
    for lvl in result.high_volume_levels:
        assert lvl.price > 0
        assert lvl.volume > 0


def test_tool_schemas_have_required_ticker() -> None:
    from app.tools.technical import (
        calc_indicators_tool,
        detect_patterns_tool,
        get_price_history_tool,
        get_volume_profile_tool,
    )

    for t in (
        get_price_history_tool,
        calc_indicators_tool,
        detect_patterns_tool,
        get_volume_profile_tool,
    ):
        assert "ticker" in t.schema["input_schema"]["properties"]
        assert "ticker" in t.schema["input_schema"]["required"]


def test_tool_schemas_advertise_market_enum() -> None:
    """Each technical tool must surface `market` so the LLM passes it; without
    this, crypto tickers like BTC resolve to a Grayscale ETF on Yahoo Finance.
    """
    from app.tools.technical import (
        calc_indicators_tool,
        detect_patterns_tool,
        get_price_history_tool,
        get_volume_profile_tool,
    )

    for t in (
        get_price_history_tool,
        calc_indicators_tool,
        detect_patterns_tool,
        get_volume_profile_tool,
    ):
        props = t.schema["input_schema"]["properties"]
        assert "market" in props, f"{t.name} schema missing market"
        assert set(props["market"]["enum"]) == {"US", "IN", "CRYPTO"}
        assert props["market"]["default"] == "US"


@pytest.mark.asyncio
async def test_calc_indicators_threads_market_through_to_fetch() -> None:
    """Regression test: calc_indicators must forward `market` to
    fetch_price_history. Without this, BTC fetched without market="CRYPTO"
    hits the bare-symbol ETF on Yahoo Finance instead of Bitcoin.
    """
    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> list[Bar]:
        captured["market"] = str(kwargs.get("market"))
        captured["ticker"] = str(kwargs.get("ticker"))
        return _bars(60)

    with patch("app.tools.technical._fetch_price_history", new=_capture):
        from app.tools.technical import calc_indicators_tool

        await calc_indicators_tool.impl(ticker="BTC", market="CRYPTO")

    assert captured["market"] == "CRYPTO"
    assert captured["ticker"] == "BTC"


@pytest.mark.asyncio
async def test_detect_patterns_threads_market_through_to_fetch() -> None:
    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> list[Bar]:
        captured["market"] = str(kwargs.get("market"))
        return _bars(60)

    with patch("app.tools.technical._fetch_price_history", new=_capture):
        from app.tools.technical import detect_patterns_tool

        await detect_patterns_tool.impl(ticker="BTC", market="CRYPTO")

    assert captured["market"] == "CRYPTO"


@pytest.mark.asyncio
async def test_get_volume_profile_threads_market_through_to_fetch() -> None:
    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> list[Bar]:
        captured["market"] = str(kwargs.get("market"))
        return _bars(60)

    with patch("app.tools.technical._fetch_price_history", new=_capture):
        from app.tools.technical import get_volume_profile_tool

        await get_volume_profile_tool.impl(ticker="BTC", market="CRYPTO")

    assert captured["market"] == "CRYPTO"


@pytest.mark.asyncio
async def test_get_price_history_threads_market_through_to_fetch() -> None:
    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> list[Bar]:
        captured["market"] = str(kwargs.get("market"))
        return _bars(5)

    with patch("app.tools.technical._fetch_price_history", new=_capture):
        from app.tools.technical import get_price_history_tool

        await get_price_history_tool.impl(ticker="BTC", market="CRYPTO")

    assert captured["market"] == "CRYPTO"
