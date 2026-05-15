# backend/tests/test_lead_banker_agent.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


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
    """Single-turn happy path: model goes straight to emit_* without dispatching."""
    from app.agents.ticker_resolver import TickerResolution
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_message(
        _tool_use("emit_quick_take", {"signal": "hold", "qualifier": "watching"}, "1"),
        _tool_use("emit_stock_card", {
            "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
            "currency": "USD", "stats": {"P/E": "29.5"},
        }, "2"),
        _tool_use("emit_section", {
            "title": "Thesis", "markdown": "Wait and see.", "citations": [],
        }, "3"),
        _tool_use("emit_section", {
            "title": "Fundamentals", "markdown": "Watching.", "citations": [],
        }, "4"),
        _tool_use("emit_section", {"title": "Risks", "markdown": "Macro.", "citations": []}, "5"),
        _tool_use("emit_recommendation", {
            "signal": "hold", "position_size_range": [0, 0],
            "entry_zone": "n/a", "stop": "n/a", "target_12mo_base": "n/a",
        }, "6"),
        _tool_use("emit_disclaimer", {}, "7"),
        _tool_use("emit_done", {}, "8"),
        stop_reason="end_turn",
    ))

    from app.agents.lead_banker import run_lead_banker
    deltas = [d async for d in run_lead_banker(
        user_message="should I buy AAPL?",
        resolution=resolution,
        client=fake_client,
    )]
    assert [d["type"] for d in deltas] == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]


@pytest.mark.asyncio
async def test_lead_banker_dispatches_specialists_in_parallel() -> None:
    """When Lead Banker emits a dispatch_specialists tool_use,
    the loop runs both Fundamental + Technical and feeds back combined findings.
    """
    from app.agents.fundamental import Citation, FundamentalFindings
    from app.agents.technical import TechnicalFinding
    from app.agents.ticker_resolver import TickerResolution

    fundamental = FundamentalFindings(
        ticker="AAPL", thesis="Strong fundamentals.",
        fundamentals_summary=["Net margin 25.5%"], risks=["Valuation rich"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    technical = TechnicalFinding(
        ticker="AAPL", trend="uptrend", rsi_14=62.0,
        macd_signal="bullish", key_levels=[210.0, 230.0],
        pattern_notes=["RSI 62 neutral-bullish"],
        citations=[Citation(source="yfinance", ref="yfinance:indicators:AAPL")],
        confidence=0.9,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    # Turn 1: model emits dispatch_specialists
    # Turn 2: after receiving tool_result, model emits the standard emit_* chain
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_specialists", {
            "specialists": ["fundamental", "technical"],
            "brief": "AAPL deep dive",
        }, "d1"), stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {
                "signal": "tactical_buy",
                "qualifier": "trend up + fundamentals strong",
            }, "1"),
            _tool_use("emit_stock_card", {
                "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
                "currency": "USD", "stats": {"RSI": "62"},
            }, "2"),
            _tool_use("emit_section", {
                "title": "Thesis", "markdown": "Trend up + fundamentals strong.",
                "citations": [],
            }, "3"),
            _tool_use("emit_section", {
                "title": "Technicals", "markdown": "RSI 62 [1].",
                "citations": [
                    {"source": "yfinance", "ref": "yfinance:indicators:AAPL", "index": 1},
                ],
            }, "4"),
            _tool_use("emit_section", {
                "title": "Risks", "markdown": "Valuation rich.",
                "citations": [],
            }, "5"),
            _tool_use("emit_recommendation", {
                "signal": "tactical_buy", "position_size_range": [2, 4],
                "entry_zone": "210-220", "stop": "200", "target_12mo_base": "260",
            }, "6"),
            _tool_use("emit_disclaimer", {}, "7"),
            _tool_use("emit_done", {}, "8"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_fundamental_analysis", AsyncMock(return_value=fundamental))
        mp.setattr(lb, "run_technical_analysis", AsyncMock(return_value=technical))

        from app.agents.lead_banker import run_lead_banker
        deltas = []
        async for d in run_lead_banker(
            user_message="deep dive on AAPL",
            resolution=resolution,
            client=fake_client,
        ):
            deltas.append(d)

    types = [d["type"] for d in deltas]
    assert types == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]
    # Verify both specialists were actually called
    assert fake_client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_lead_banker_dispatches_all_four_specialists() -> None:
    """Verify the dispatch tool accepts and runs news + macro alongside fundamental + technical."""
    from app.agents.fundamental import Citation, FundamentalFindings
    from app.agents.macro import MacroFindings
    from app.agents.news_sentiment import NewsFindings
    from app.agents.technical import TechnicalFinding
    from app.agents.ticker_resolver import TickerResolution

    fundamental = FundamentalFindings(
        ticker="AAPL", thesis="Strong.",
        fundamentals_summary=["Margin 25%"], risks=["Valuation"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    technical = TechnicalFinding(
        ticker="AAPL", trend="uptrend", rsi_14=62.0,
        macd_signal="bullish", key_levels=[210.0, 230.0],
        pattern_notes=["RSI 62"], citations=[], confidence=0.85,
    )
    news = NewsFindings(
        ticker="AAPL", headline_count=8, sentiment="positive",
        catalysts=["Services growth"], notable_headlines=["Reuters: AAPL up (May 14)"],
        citations=[Citation(source="yfinance", ref="yahoo:news:AAPL")],
        confidence=0.85,
    )
    macro = MacroFindings(
        regime="neutral",
        rates_snapshot={"fed_funds": 5.25, "treasury_10y": 4.42},
        sector_performance={"XLK": 0.04},
        macro_notes=["Restrictive zone"],
        citations=[Citation(source="fred", ref="FRED:FEDFUNDS")],
        confidence=0.85,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_specialists", {
            "specialists": ["fundamental", "technical", "news", "macro"],
            "brief": "AAPL full deep dive",
        }, "d1"), stop_reason="tool_use"),
        _message(
            _tool_use(
                "emit_quick_take",
                {"signal": "tactical_buy", "qualifier": "all signals aligned"},
                "1",
            ),
            _tool_use("emit_stock_card", {
                "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
                "currency": "USD", "stats": {"P/E": "29", "RSI": "62"},
            }, "2"),
            _tool_use(
                "emit_section",
                {"title": "Thesis", "markdown": "Bullish.", "citations": []},
                "3",
            ),
            _tool_use(
                "emit_section",
                {"title": "Fundamentals", "markdown": "Strong.", "citations": []},
                "4",
            ),
            _tool_use(
                "emit_section",
                {"title": "Technicals", "markdown": "Uptrend.", "citations": []},
                "5",
            ),
            _tool_use(
                "emit_section",
                {"title": "News", "markdown": "Positive headlines.", "citations": []},
                "6",
            ),
            _tool_use(
                "emit_section",
                {"title": "Macro", "markdown": "Neutral regime.", "citations": []},
                "7",
            ),
            _tool_use(
                "emit_section",
                {"title": "Risks", "markdown": "Valuation.", "citations": []},
                "8",
            ),
            _tool_use("emit_recommendation", {
                "signal": "tactical_buy", "position_size_range": [2, 4],
                "entry_zone": "215-225", "stop": "200", "target_12mo_base": "260",
            }, "9"),
            _tool_use("emit_disclaimer", {}, "10"),
            _tool_use("emit_done", {}, "11"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_fundamental_analysis", AsyncMock(return_value=fundamental))
        mp.setattr(lb, "run_technical_analysis", AsyncMock(return_value=technical))
        mp.setattr(lb, "run_news_analysis", AsyncMock(return_value=news))
        mp.setattr(lb, "run_macro_analysis", AsyncMock(return_value=macro))

        from app.agents.lead_banker import run_lead_banker
        deltas = [d async for d in run_lead_banker(
            user_message="deep dive on AAPL",
            resolution=resolution,
            client=fake_client,
        )]

    section_titles = [d["title"] for d in deltas if d.get("type") == "section"]
    assert section_titles == ["Thesis", "Fundamentals", "Technicals", "News", "Macro", "Risks"]
    types = [d["type"] for d in deltas]
    assert types[-1] == "done"
