# backend/tests/test_tools_news.py
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import NewsItem


@pytest.mark.asyncio
async def test_search_news_tool_wraps_adapter() -> None:
    fake = [
        NewsItem(
            title="Apple unveils new iPhone",
            publisher="Reuters",
            url="https://reuters.com/aapl-iphone",
            published_at=datetime(2026, 5, 14, 16, 0, tzinfo=UTC),
            summary=None,
            related_tickers=["AAPL"],
        ),
    ]
    with patch("app.tools.news._fetch_news_for_ticker", new=AsyncMock(return_value=fake)):
        from app.tools.news import NewsResult, search_news_tool
        result = cast(NewsResult, await search_news_tool.impl(ticker="AAPL", limit=5))
    assert result.ticker == "AAPL"
    assert len(result.items) == 1
    assert result.items[0].title == "Apple unveils new iPhone"


def test_search_news_tool_schema() -> None:
    from app.tools.news import search_news_tool
    s = search_news_tool.schema
    assert s["name"] == "search_news"
    assert "ticker" in s["input_schema"]["properties"]
    assert "limit" in s["input_schema"]["properties"]
    assert s["input_schema"]["required"] == ["ticker"]


def test_search_news_tool_schema_advertises_market_enum() -> None:
    """The tool must surface `market` so the LLM passes it; without this,
    crypto tickers like BTC resolve to a Grayscale ETF on Yahoo Finance.
    """
    from app.tools.news import search_news_tool
    props = search_news_tool.schema["input_schema"]["properties"]
    assert "market" in props
    assert set(props["market"]["enum"]) == {"US", "IN", "CRYPTO"}
    assert props["market"]["default"] == "US"


@pytest.mark.asyncio
async def test_search_news_threads_market_through_to_fetch() -> None:
    """Regression: search_news must forward `market` to fetch_news_for_ticker.
    Without this, BTC news fetched without market="CRYPTO" returns Grayscale
    ETF news instead of Bitcoin news.
    """
    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> list[NewsItem]:
        captured["market"] = str(kwargs.get("market"))
        captured["ticker"] = str(kwargs.get("ticker"))
        return []

    with patch("app.tools.news._fetch_news_for_ticker", new=_capture):
        from app.tools.news import search_news_tool
        await search_news_tool.impl(ticker="BTC", market="CRYPTO")

    assert captured["market"] == "CRYPTO"
    assert captured["ticker"] == "BTC"
