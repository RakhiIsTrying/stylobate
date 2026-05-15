# backend/tests/test_macro_agent.py
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
async def test_macro_agent_runs_tool_loop_to_submit() -> None:
    from app.tools.macro import (
        RatesResult,
        SectorPerfResult,
        get_rates_tool,
        get_sector_perf_tool,
    )

    fake_rates = RatesResult(
        market="US",
        fed_funds=5.25, treasury_2y=4.87, treasury_10y=4.42, real_10y=2.10,
    )
    fake_sectors = SectorPerfResult(
        period="1mo",
        returns={"XLK": 0.04, "XLF": 0.01, "XLV": -0.02},
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_rates", {}, "m1")),
        _message(_tool_use_block("get_sector_perf", {"period": "1mo"}, "m2")),
        _message(
            _tool_use_block("submit_macro_findings", {
                "regime": "neutral",
                "rates_snapshot": {
                    "fed_funds": 5.25, "treasury_2y": 4.87,
                    "treasury_10y": 4.42, "real_10y": 2.10,
                },
                "sector_performance": {"XLK": 0.04, "XLF": 0.01, "XLV": -0.02},
                "macro_notes": [
                    "Fed funds held at 5.25% — restrictive zone",
                    "10Y-2Y inverted by 45bps; mild recession signal",
                ],
                "citations": [{"source": "fred", "ref": "FRED:FEDFUNDS,DGS2,DGS10,DFII10"}],
                "confidence": 0.9,
            }, "m3"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_rates_tool, "impl", AsyncMock(return_value=fake_rates))
        mp.setattr(get_sector_perf_tool, "impl", AsyncMock(return_value=fake_sectors))
        from app.agents.macro import run_macro_analysis
        findings = await run_macro_analysis(
            ticker="AAPL", brief="quick macro check", client=fake_client
        )

    assert findings.regime == "neutral"
    assert findings.rates_snapshot["treasury_10y"] == 4.42
    assert findings.sector_performance["XLK"] == 0.04
    assert findings.confidence == 0.9


@pytest.mark.asyncio
async def test_macro_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.macro import MacroError, run_macro_analysis
    with pytest.raises(MacroError):
        await run_macro_analysis(ticker="AAPL", brief="x", client=fake_client)


@pytest.mark.asyncio
async def test_macro_agent_passes_market_to_get_rates() -> None:
    """When run_macro_analysis is called with market=IN, the user message
    surfaces market=IN so the model can include it in the get_rates call."""
    from app.tools.macro import (
        RatesResult,
        get_rates_tool,
        get_sector_perf_tool,
    )

    fake_rates = RatesResult(
        market="IN", policy_rate=6.50, long_yield=7.10, inflation=4.80,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_rates", {"market": "IN"}, "r1")),
        _message(
            _tool_use_block("submit_macro_findings", {
                "regime": "neutral",
                "rates_snapshot": {"policy_rate": 6.50, "long_yield": 7.10, "inflation": 4.80},
                "sector_performance": {},
                "macro_notes": ["Repo at 6.50% — restrictive zone"],
                "citations": [{"source": "fred", "ref": "FRED:INDIRSTPRLR01STM,IRLTLT01INM156N"}],
                "confidence": 0.85,
            }, "r2"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_rates_tool, "impl", AsyncMock(return_value=fake_rates))
        # sector tool shouldn't be called for IN — make it raise if it is
        mp.setattr(get_sector_perf_tool, "impl",
                   AsyncMock(side_effect=AssertionError("sector_perf must not be called for IN")))
        from app.agents.macro import run_macro_analysis
        findings = await run_macro_analysis(
            ticker="RELIANCE.NS", brief="Indian deep dive",
            market="IN", client=fake_client,
        )
    # Verify the user message carried the market context (case-insensitive)
    first_call = fake_client.messages.create.await_args_list[0]
    user_message_content = first_call.kwargs["messages"][0]["content"]
    lowered = user_message_content.lower()
    assert "market: in" in lowered or "market=in" in lowered
    assert findings.regime == "neutral"
