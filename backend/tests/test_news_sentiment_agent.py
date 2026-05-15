# backend/tests/test_news_sentiment_agent.py
import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use_block(name: str, args: dict[str, Any], tool_id: str = "t1") -> Any:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = args
    block.id = tool_id
    return block


def _message(*blocks: Any, stop_reason: str = "tool_use") -> Any:
    msg = MagicMock()
    msg.content = list(blocks)
    msg.stop_reason = stop_reason
    msg.usage = MagicMock(
        input_tokens=100, output_tokens=20,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return msg


@pytest.mark.asyncio
async def test_news_agent_runs_tool_loop_to_submit() -> None:
    from app.data.yfinance_adapter import NewsItem
    from app.tools.news import NewsResult, search_news_tool

    fake_news = NewsResult(
        ticker="AAPL",
        items=[
            NewsItem(
                title="Apple Services beats estimates",
                publisher="Bloomberg",
                url="https://bloomberg.com/aapl-services",
                published_at=dt.datetime(2026, 5, 14, 16, 0, tzinfo=dt.UTC),
                summary="Services up 17% YoY.",
                related_tickers=["AAPL"],
            ),
        ],
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("search_news", {"ticker": "AAPL", "limit": 10}, "n1")),
        _message(
            _tool_use_block("submit_news_findings", {
                "ticker": "AAPL",
                "headline_count": 1,
                "sentiment": "positive",
                "catalysts": ["Services growth durability"],
                "notable_headlines": ["Bloomberg: Apple Services beats estimates (May 14)"],
                "citations": [{"source": "bloomberg", "ref": "https://bloomberg.com/aapl-services"}],
                "confidence": 0.85,
            }, "n2"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(search_news_tool, "impl", AsyncMock(return_value=fake_news))
        from app.agents.news_sentiment import run_news_analysis
        findings = await run_news_analysis(ticker="AAPL", brief="news check", client=fake_client)

    assert findings.ticker == "AAPL"
    assert findings.sentiment == "positive"
    assert findings.headline_count == 1
    assert len(findings.notable_headlines) == 1


@pytest.mark.asyncio
async def test_news_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.news_sentiment import NewsError, run_news_analysis
    with pytest.raises(NewsError):
        await run_news_analysis(ticker="AAPL", brief="x", client=fake_client)
