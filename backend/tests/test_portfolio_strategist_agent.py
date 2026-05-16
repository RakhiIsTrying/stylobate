from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*content_blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = content_blocks
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_strategist_dispatches_get_holdings_then_submits() -> None:
    """LLM calls get_holdings, then submit_portfolio_findings; agent returns findings."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id="p-1")
    # Patch impls so we don't hit the DB
    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "holdings_count": 1,
                                              "positions": [{"ticker": "AAPL"}]})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "My Portfolio",
                "cohorts": [],
                "rebalance": None,
                "notes": ["empty cohort list (no positions)"],
                "citations": [],
                "confidence": 0.7,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    findings = await run_portfolio_strategist(
        user_id="u-1", portfolio_id="p-1",
        brief="snapshot",
        target_alloc=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["portfolio_id"] == "p-1"
    assert findings["confidence"] == 0.7


@pytest.mark.asyncio
async def test_strategist_short_circuits_when_no_portfolios() -> None:
    """If get_holdings returns 0 holdings AND no portfolio_id, agent emits the empty path."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id=None)
    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": None, "holdings_count": 0,
                                              "positions": []})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": None,
                "portfolio_name": "",
                "cohorts": [],
                "rebalance": None,
                "notes": ["You don't have a portfolio yet. Visit /portfolio to create one."],
                "citations": [],
                "confidence": 1.0,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    findings = await run_portfolio_strategist(
        user_id="u-1", portfolio_id=None,
        brief="snapshot",
        target_alloc=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["portfolio_id"] is None
    assert "create one" in findings["notes"][0]


@pytest.mark.asyncio
async def test_strategist_passes_target_alloc_to_rebalance_tool() -> None:
    """When brief mentions rebalance + target_alloc passed in, the agent's calls reach
    suggest_rebalance with the target dict."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id="p-1")
    rebalance_calls: list[dict[str, Any]] = []

    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "holdings_count": 1,
                                              "positions": [{"ticker": "AAPL"}]})
        elif t.name == "suggest_rebalance":
            async def _capture(**kwargs: Any) -> dict[str, Any]:
                rebalance_calls.append(kwargs)
                return {"sum_check_ok": True, "cohort_trades": []}
            t.impl = _capture
        else:
            t.impl = AsyncMock(return_value={})

    target = {"USD:equity_etf": 0.6, "INR:equity_etf": 0.4}
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(_tool_use("suggest_rebalance", {"target_alloc": target}, "r1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "My Portfolio",
                "cohorts": [],
                "rebalance": None,
                "notes": [],
                "citations": [],
                "confidence": 0.8,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    await run_portfolio_strategist(
        user_id="u-1", portfolio_id="p-1",
        brief="rebalance to 60/40 USD/INR",
        target_alloc=target,
        client=fake_client,
        tools_override=tools,
    )
    assert len(rebalance_calls) == 1
    assert rebalance_calls[0]["target_alloc"] == target
