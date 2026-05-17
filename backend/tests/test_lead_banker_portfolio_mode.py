from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_lead_banker_portfolio_mode_calls_strategist_not_specialists() -> None:
    """In portfolio_mode, Lead Banker uses dispatch_portfolio_strategist."""
    captured_findings_call: list[dict[str, Any]] = []

    async def fake_strategist(**kwargs: Any) -> dict[str, Any]:
        captured_findings_call.append(kwargs)
        return {
            "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
            "cohorts": [
                {"cohort": ["USD", "equity_etf"], "positions_count": 1,
                 "total_value_native": 2000.0, "total_cost_native": 1500.0,
                 "gain_pct": 33.3, "weights": {"AAPL": 1.0},
                 "returns_1mo": 5.0, "returns_3mo": 12.0, "returns_1y": 30.0,
                 "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 18.0,
                 "sharpe_1y": 1.2, "beta_1y": 1.05, "max_drawdown_1y": -0.08,
                 "prices_partial": False},
            ],
            "rebalance": None, "notes": [], "citations": [], "confidence": 0.85,
        }

    fake_client = MagicMock()
    # Lead Banker turn 1: dispatch_portfolio_strategist
    # Lead Banker turn 2: emit_* tools, end_turn
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_strategist",
                           {"brief": "Snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tool_use("emit_section", {"title": "Portfolio Snapshot",
                                        "markdown": "- AAPL 100%", "citations": []}, "2"),
            _tool_use("emit_section", {"title": "Risks", "markdown": "Concentrated.",
                                        "citations": []}, "3"),
            _tool_use("emit_disclaimer", {}, "4"),
            _tool_use("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)

        deltas: list[dict[str, Any]] = []
        async for d in lb.run_lead_banker(
            user_message="How is my portfolio doing?",
            resolution=None,
            client=fake_client,
            portfolio_mode=True,
            user_id="u-1",
        ):
            deltas.append(d)

    # Strategist was called exactly once
    assert len(captured_findings_call) == 1
    assert captured_findings_call[0]["user_id"] == "u-1"
    # Final stream contains the emitted sections
    section_titles = [d.get("title") for d in deltas if d.get("type") == "section"]
    assert "Portfolio Snapshot" in section_titles
