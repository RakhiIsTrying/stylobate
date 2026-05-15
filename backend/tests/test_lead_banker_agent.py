# backend/tests/test_lead_banker_agent.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.fundamental import Citation, FundamentalFindings
from app.agents.ticker_resolver import TickerResolution


def _tool_use(name: str, args: dict[str, Any], id_: str) -> Any:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = args
    block.id = id_
    return block


def _message(*blocks: Any, stop_reason: str = "end_turn") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=100, output_tokens=50,
                        cache_creation_input_tokens=80, cache_read_input_tokens=20)
    return m


@pytest.mark.asyncio
async def test_lead_banker_streams_expected_deltas() -> None:
    findings = FundamentalFindings(
        ticker="AAPL",
        thesis="Strong cash generation.",
        fundamentals_summary=["Net margin 25.5%"],
        risks=["P/E rich"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_message(
        _tool_use(
            "emit_quick_take",
            {"signal": "tactical_buy", "qualifier": "valuation rich"},
            "1",
        ),
        _tool_use("emit_stock_card", {
            "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
            "currency": "USD", "stats": {"P/E": "29.5", "Net margin": "25.5%"},
        }, "2"),
        _tool_use("emit_section", {
            "title": "Thesis",
            "markdown": "Strong cash generation [1].",
            "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}],
        }, "3"),
        _tool_use("emit_section", {
            "title": "Fundamentals",
            "markdown": "Net margin 25.5% [1].",
            "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}],
        }, "4"),
        _tool_use("emit_section", {
            "title": "Risks",
            "markdown": "P/E rich.",
            "citations": [],
        }, "5"),
        _tool_use("emit_recommendation", {
            "signal": "tactical_buy",
            "position_size_range": [2, 4],
            "entry_zone": "440-455",
            "stop": "385",
            "target_12mo_base": "540",
        }, "6"),
        _tool_use("emit_disclaimer", {}, "7"),
        _tool_use("emit_done", {}, "8"),
        stop_reason="end_turn",
    ))

    from app.agents.lead_banker import run_lead_banker
    deltas = []
    async for d in run_lead_banker(
        user_message="Should I buy AAPL?",
        resolution=resolution,
        fundamental_findings=findings,
        client=fake_client,
    ):
        deltas.append(d)

    types = [d["type"] for d in deltas]
    assert types == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]
    # Section titles
    sections = [d for d in deltas if d["type"] == "section"]
    assert [s["title"] for s in sections] == ["Thesis", "Fundamentals", "Risks"]
