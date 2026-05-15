# backend/tests/test_technical_agent.py
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
async def test_technical_agent_runs_tool_loop_to_submit_findings() -> None:
    from app.tools.technical import (
        IndicatorsResult,
        PatternLevel,
        PatternsResult,
        VolumeLevel,
        VolumeProfileResult,
        calc_indicators_tool,
        detect_patterns_tool,
        get_volume_profile_tool,
    )

    fake_indicators = IndicatorsResult(
        ticker="AAPL", period="1y",
        latest={"sma200": 200.0, "ema50": 215.0, "rsi14": 62.0,
                "macd_line": 1.5, "macd_signal": 1.2, "macd_histogram": 0.3,
                "bbands_upper": 230.0, "bbands_middle": 220.0, "bbands_lower": 210.0},
    )
    fake_patterns = PatternsResult(
        ticker="AAPL",
        levels=[
            PatternLevel(kind="support", price=210.0, touches=3),
            PatternLevel(kind="resistance", price=230.0, touches=2),
        ],
    )
    fake_volume = VolumeProfileResult(
        ticker="AAPL",
        high_volume_levels=[VolumeLevel(price=220.0, volume=500_000_000)],
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("calc_indicators", {"ticker": "AAPL"}, "c1")),
        _message(_tool_use_block("detect_patterns", {"ticker": "AAPL"}, "c2")),
        _message(_tool_use_block("get_volume_profile", {"ticker": "AAPL"}, "c3")),
        _message(
            _tool_use_block("submit_technical_findings", {
                "ticker": "AAPL",
                "trend": "uptrend",
                "rsi_14": 62.0,
                "macd_signal": "bullish",
                "key_levels": [210.0, 230.0],
                "pattern_notes": [
                    "Price above sma200 (220 vs 200), uptrend confirmed",
                    "RSI 62 — moderately bullish, not yet overbought",
                    "MACD histogram positive (0.3)",
                ],
                "citations": [{"source": "yfinance", "ref": "yfinance:indicators:AAPL"}],
                "confidence": 0.9,
            }, "c4"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(calc_indicators_tool, "impl", AsyncMock(return_value=fake_indicators))
        mp.setattr(detect_patterns_tool, "impl", AsyncMock(return_value=fake_patterns))
        mp.setattr(get_volume_profile_tool, "impl", AsyncMock(return_value=fake_volume))

        from app.agents.technical import run_technical_analysis
        findings = await run_technical_analysis(
            ticker="AAPL", brief="quick technical", client=fake_client
        )

    assert findings.ticker == "AAPL"
    assert findings.trend == "uptrend"
    assert findings.macd_signal == "bullish"
    assert findings.key_levels == [210.0, 230.0]
    assert findings.confidence == 0.9


@pytest.mark.asyncio
async def test_technical_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.technical import TechnicalError, run_technical_analysis
    with pytest.raises(TechnicalError):
        await run_technical_analysis(ticker="AAPL", brief="x", client=fake_client)


@pytest.mark.asyncio
async def test_technical_agent_surfaces_market_in_user_message() -> None:
    """When run_technical_analysis is called with market="CRYPTO", the user
    message must surface market=CRYPTO so the LLM passes it to every tool
    call (otherwise yfinance resolves BTC to a Grayscale ETF, not Bitcoin).
    """
    from app.tools.technical import (
        IndicatorsResult,
        calc_indicators_tool,
        detect_patterns_tool,
        get_volume_profile_tool,
    )

    fake_indicators = IndicatorsResult(
        ticker="BTC", period="1y",
        latest={"sma200": 60000.0, "rsi14": 55.0},
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(
            _tool_use_block("submit_technical_findings", {
                "ticker": "BTC",
                "trend": "uptrend",
                "rsi_14": 55.0,
                "key_levels": [],
                "pattern_notes": ["uptrend"],
                "citations": [{"source": "yfinance", "ref": "yfinance:BTC"}],
                "confidence": 0.8,
            }, "c1"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(calc_indicators_tool, "impl", AsyncMock(return_value=fake_indicators))
        mp.setattr(detect_patterns_tool, "impl", AsyncMock())
        mp.setattr(get_volume_profile_tool, "impl", AsyncMock())

        from app.agents.technical import run_technical_analysis
        await run_technical_analysis(
            ticker="BTC", brief="crypto check",
            market="CRYPTO", client=fake_client,
        )

    first_call = fake_client.messages.create.await_args_list[0]
    user_message_content = first_call.kwargs["messages"][0]["content"]
    lowered = user_message_content.lower()
    assert "market: crypto" in lowered or "market=crypto" in lowered
    # And the prompt instructs the model to pass market on tool calls
    assert "crypto" in user_message_content
