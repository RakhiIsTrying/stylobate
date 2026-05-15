# backend/tests/test_fundamental_agent.py
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.edgar import FilingRef
from app.data.yfinance_adapter import Financials, KeyRatios


def _tool_use_block(name: str, args: dict[str, Any], tool_id: str = "toolu_1") -> Any:
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
        cache_creation_input_tokens=80, cache_read_input_tokens=0,
    )
    return msg


@pytest.mark.asyncio
async def test_fundamental_agent_runs_tool_loop_to_submit_findings() -> None:
    # Turn 1: model calls get_key_ratios
    # Turn 2: model calls get_financials
    # Turn 3: model calls get_filings
    # Turn 4: model submits findings
    fake_ratios = KeyRatios(
        ticker="AAPL", as_of=date(2026, 5, 14),
        pe_ttm=29.5, pb=50.1, ps_ttm=8.8, roe=1.45,
        roic=0.30, fcf_yield=None, debt_to_equity=152.0,
        current_ratio=0.98, net_margin=0.255, revenue_growth_yoy=0.062,
    )
    fake_financials = [
        Financials(ticker="AAPL", period="FY2024", revenue=391e9,
                   net_income=93.7e9, operating_cash_flow=118e9,
                   free_cash_flow=108e9, currency="USD"),
    ]
    fake_filings = [
        FilingRef(type="10-K", date=date(2024, 11, 1),
                  url="https://sec.gov/edgar/aapl-10k.htm",
                  accession="0000320193-24-000123"),
    ]

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_key_ratios", {"ticker": "AAPL"}, "t1")),
        _message(_tool_use_block("get_financials", {"ticker": "AAPL", "periods": 2}, "t2")),
        _message(_tool_use_block("get_filings", {"ticker": "AAPL", "limit": 3}, "t3")),
        _message(
            _tool_use_block("submit_findings", {
                "ticker": "AAPL",
                "thesis": "Strong cash generation; valuation rich vs history.",
                "fundamentals_summary": [
                    "Net margin 25.5% (yfinance)",
                    "Revenue FY24 $391B, +2% YoY",
                ],
                "risks": [
                    "P/E 29.5x vs 5y avg ~22x",
                    "China demand softness referenced in 10-K",
                ],
                "citations": [
                    {"source": "yfinance", "ref": "yfinance:ratios:AAPL"},
                    {"source": "edgar", "ref": "AAPL 10-K 2024-11-01"},
                ],
                "confidence": 0.9,
            }, "t4"),
            stop_reason="tool_use",
        ),
    ])

    # Patch the tool implementations to return our fakes via monkeypatch
    with pytest.MonkeyPatch.context() as mp:
        from app.tools.filings import FilingsResult, get_filings_tool
        from app.tools.financials import (
            FinancialsResult,
            get_financials_tool,
            get_key_ratios_tool,
        )

        mp.setattr(
            get_key_ratios_tool, "impl",
            AsyncMock(return_value=fake_ratios),
        )
        mp.setattr(
            get_financials_tool, "impl",
            AsyncMock(return_value=FinancialsResult(ticker="AAPL", periods=fake_financials)),
        )
        mp.setattr(
            get_filings_tool, "impl",
            AsyncMock(return_value=FilingsResult(ticker="AAPL", filings=fake_filings)),
        )

        from app.agents.fundamental import run_fundamental_analysis
        findings = await run_fundamental_analysis(
            ticker="AAPL",
            brief="Quick fundamentals review",
            client=fake_client,
        )

    assert findings.ticker == "AAPL"
    assert findings.confidence == 0.9
    assert len(findings.citations) == 2
    # Verify the tool loop ran exactly 4 messages.create calls
    assert fake_client.messages.create.await_count == 4


@pytest.mark.asyncio
async def test_fundamental_agent_raises_if_no_submit_findings() -> None:
    fake_client = MagicMock()
    # Model just keeps emitting text without ever calling submit_findings
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.fundamental import FundamentalError, run_fundamental_analysis
    with pytest.raises(FundamentalError):
        await run_fundamental_analysis(ticker="AAPL", brief="hi", client=fake_client)


@pytest.mark.asyncio
async def test_fundamental_agent_short_circuits_for_crypto() -> None:
    """For market=CRYPTO, no LLM call happens — agent returns a graceful empty finding."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        side_effect=AssertionError("no LLM call should happen for crypto fundamentals"),
    )
    from app.agents.fundamental import run_fundamental_analysis
    findings = await run_fundamental_analysis(
        ticker="BTC", brief="deep dive on BTC", market="CRYPTO", client=fake_client,
    )
    assert findings.ticker == "BTC"
    assert findings.confidence == 0.0
    # Some short transparent message indicating no financial statements
    assert any(
        "no traditional" in s.lower() or "not applicable" in s.lower() or "n/a" in s.lower()
        for s in [*findings.fundamentals_summary, findings.thesis]
    )
    # Verify the LLM was never called
    fake_client.messages.create.assert_not_called()
