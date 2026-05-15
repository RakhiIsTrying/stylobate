# backend/app/tools/news.py
from pydantic import BaseModel

from app.data.yfinance_adapter import (
    NewsItem,
)
from app.data.yfinance_adapter import (
    fetch_news_for_ticker as _fetch_news_for_ticker,
)
from app.tools.base import Tool


class NewsResult(BaseModel):
    ticker: str
    items: list[NewsItem]


async def _impl_search_news(ticker: str, limit: int = 10) -> NewsResult:
    items = await _fetch_news_for_ticker(ticker=ticker, limit=limit)
    return NewsResult(ticker=ticker.upper(), items=items)


search_news_tool = Tool(
    name="search_news",
    description=(
        "Recent news headlines for the ticker. Returns title, publisher, URL, "
        "published_at, optional summary, and related tickers. Default limit 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
        },
        "required": ["ticker"],
    },
    impl=_impl_search_news,
)
