from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*content_blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(content_blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_risk_manager_runs_all_tools_then_submits() -> None:
    """The agent calls concentration_check, calc_var, get_correlations, stress_test x3,
    then submit_risk_findings exactly once."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id="p-1")
    for t in tools:
        if t.name == "concentration_check":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "flags": []})
        elif t.name == "calc_var":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "var_by_cohort": []})
        elif t.name == "get_correlations":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "tickers": [],
                                              "matrix": [], "excluded": []})
        elif t.name == "stress_test":
            t.impl = AsyncMock(return_value={"scenario": "x", "per_position": [],
                                              "by_cohort": [], "total_delta_usd": 0.0,
                                              "assumed_fx": {}})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("concentration_check", {}, "c1")),
        _message(_tool_use("calc_var", {}, "v1")),
        _message(_tool_use("get_correlations", {}, "x1")),
        _message(_tool_use("stress_test", {"scenario": "rates_+200bps"}, "s1")),
        _message(_tool_use("stress_test", {"scenario": "equity_-20%"}, "s2")),
        _message(_tool_use("stress_test", {"scenario": "inr_depreciation_-10%"}, "s3")),
        _message(
            _tool_use("submit_risk_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "Test",
                "concentration": [],
                "var_by_cohort": [],
                "correlations": None,
                "stress_results": [],
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "f1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id="p-1", brief="full risk analysis",
        client=fake_client, tools_override=tools,
    )
    assert findings["portfolio_id"] == "p-1"
    assert findings["confidence"] == 0.85
    # Verify all tools were exercised
    conc = next(t for t in tools if t.name == "concentration_check")
    stress = next(t for t in tools if t.name == "stress_test")
    assert cast(AsyncMock, conc.impl).await_count == 1
    assert cast(AsyncMock, stress.impl).await_count == 3


@pytest.mark.asyncio
async def test_risk_manager_short_circuits_on_no_portfolios() -> None:
    """concentration_check returns empty flags + null portfolio_id triggers empty findings."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id=None)
    for t in tools:
        if t.name == "concentration_check":
            t.impl = AsyncMock(return_value={"portfolio_id": None, "flags": []})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("concentration_check", {}, "c1")),
        _message(
            _tool_use("submit_risk_findings", {
                "portfolio_id": None,
                "portfolio_name": "",
                "concentration": [],
                "var_by_cohort": [],
                "correlations": None,
                "stress_results": [],
                "notes": ["No portfolio to analyze. Visit /portfolio to create one."],
                "citations": [],
                "confidence": 1.0,
            }, "f1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id=None, brief="risk",
        client=fake_client, tools_override=tools,
    )
    assert findings["portfolio_id"] is None
    assert "create one" in findings["notes"][0]


@pytest.mark.asyncio
async def test_risk_manager_falls_back_when_loop_exhausts() -> None:
    """If the LLM never submits within turn budget, agent returns a default empty findings."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id="p-1")
    for t in tools:
        t.impl = AsyncMock(return_value={})

    # Mock keeps emitting useless text blocks; no submit ever
    def _text(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_text("thinking..."), stop_reason="end_turn"),
    ] * 10)

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id="p-1", brief="risk",
        client=fake_client, tools_override=tools,
    )
    assert findings["confidence"] == 0.0
    assert "turn budget" in findings["notes"][0]
