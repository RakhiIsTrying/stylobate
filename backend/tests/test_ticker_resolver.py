# backend/tests/test_ticker_resolver.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use_response(args: dict[str, Any]) -> Any:
    """Build a fake Anthropic message with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "resolve"
    block.input = args
    block.id = "toolu_test_1"
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    resp.usage = MagicMock(input_tokens=10, output_tokens=10,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


@pytest.mark.asyncio
async def test_resolve_us_stock() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
        "asset_class": "equity", "confidence": 0.95, "candidates": [],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("Apple", client=fake_client)
    assert result.ticker == "AAPL"
    assert result.market == "US"
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_resolve_ambiguous_indian() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "RELIANCE.NS", "name": "Reliance Industries Ltd.",
        "market": "IN", "asset_class": "equity", "confidence": 0.65,
        "candidates": ["RELIANCE.BO"],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("Reliance", client=fake_client)
    assert result.ticker == "RELIANCE.NS"
    assert result.candidates == ["RELIANCE.BO"]


@pytest.mark.asyncio
async def test_resolve_returns_low_confidence_for_garbage() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "", "name": "", "market": "US",
        "asset_class": "equity", "confidence": 0.0, "candidates": [],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("asdfqwer", client=fake_client)
    assert result.confidence == 0.0
    assert result.ticker == ""
