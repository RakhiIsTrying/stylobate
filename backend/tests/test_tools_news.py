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
