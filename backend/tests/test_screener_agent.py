from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*content_blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(content_blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(
        input_tokens=10, output_tokens=20,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return m


@pytest.mark.asyncio
async def test_screener_runs_resolve_theme_submit() -> None:
    """Happy path: resolve_universe → theme_to_universe → submit_screener_findings."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    for t in tools:
        if t.name == "resolve_universe":
            t.impl = AsyncMock(return_value={
                "universes": ["sp500"], "constituents": [
                    {"ticker": "AAPL", "name": "Apple"},
                ], "constituent_count": 1,
            })
        elif t.name == "theme_to_universe":
            t.impl = AsyncMock(return_value={
                "theme": "AI plays", "tickers": ["AAPL"], "dropped": [], "notes": [],
            })
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("resolve_universe", {"universes": ["sp500"]}, "r1")),
        _message(_tool_use("theme_to_universe",
                           {"theme": "AI plays", "tickers": ["AAPL"]}, "t1")),
        _message(
            _tool_use("submit_screener_findings", {
                "theme": "AI plays",
                "universes_used": ["sp500"],
                "candidates": [{"ticker": "AAPL", "name": "Apple", "market": "US",
                                "currency": "USD", "sector": "IT",
                                "current_price": 200.0, "market_cap": 3e12,
                                "pe_ttm": 35.0, "roe_pct": 120.0,
                                "revenue_growth_yoy": 10.0}],
                "filters_applied": {},
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.screener import run_screener
    findings = await run_screener(
        user_message="Find me AI plays in US",
        user_id=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["theme"] == "AI plays"
    assert findings["confidence"] == 0.85
    assert len(findings["candidates"]) == 1


@pytest.mark.asyncio
async def test_screener_with_filters_calls_screen_stocks() -> None:
    """When the user mentions numeric filters, agent calls screen_stocks."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    screen_calls: list[dict[str, Any]] = []
    for t in tools:
        if t.name == "resolve_universe":
            t.impl = AsyncMock(return_value={
                "universes": ["sp500"], "constituents": [],
                "constituent_count": 0,
            })
        elif t.name == "theme_to_universe":
            t.impl = AsyncMock(return_value={
                "theme": "dividend", "tickers": ["WMT", "JNJ"],
                "dropped": [], "notes": [],
            })
        elif t.name == "screen_stocks":
            async def _capture(**kwargs: Any) -> dict[str, Any]:
                screen_calls.append(kwargs)
                return {"candidates": [], "dropped": [], "notes": [],
                        "filters_applied": kwargs}
            t.impl = _capture
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("resolve_universe", {"universes": ["sp500"]}, "r1")),
        _message(_tool_use("theme_to_universe",
                           {"theme": "dividend", "tickers": ["WMT", "JNJ"]}, "t1")),
        _message(_tool_use("screen_stocks",
                           {"tickers": ["WMT", "JNJ"], "max_pe": 20.0}, "f1")),
        _message(
            _tool_use("submit_screener_findings", {
                "theme": "dividend", "universes_used": ["sp500"],
                "candidates": [], "filters_applied": {"max_pe": 20.0},
                "notes": [], "citations": [], "confidence": 0.7,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.screener import run_screener
    await run_screener(
        user_message="Find me cheap US dividend stocks with PE under 20",
        user_id=None, client=fake_client, tools_override=tools,
    )
    assert len(screen_calls) == 1
    assert screen_calls[0]["max_pe"] == 20.0


@pytest.mark.asyncio
async def test_screener_falls_back_when_loop_exhausts() -> None:
    """LLM that never submits → agent returns default empty findings."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    for t in tools:
        t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message({"type": "text", "text": "thinking..."}, stop_reason="end_turn"),
    ] * 10)

    from app.agents.screener import run_screener
    findings = await run_screener(
        user_message="Anything",
        user_id=None, client=fake_client, tools_override=tools,
    )
    assert findings["confidence"] == 0.0
    assert "turn budget" in findings["notes"][0]
