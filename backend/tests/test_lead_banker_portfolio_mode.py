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
async def test_lead_banker_portfolio_mode_dispatches_both_analysts() -> None:
    """portfolio_mode uses dispatch_portfolio_analysts; both strategist + risk run."""
    captured_strategist: list[dict[str, Any]] = []
    captured_risk: list[dict[str, Any]] = []

    async def fake_strategist(**kwargs: Any) -> dict[str, Any]:
        captured_strategist.append(kwargs)
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

    async def fake_risk(**kwargs: Any) -> dict[str, Any]:
        captured_risk.append(kwargs)
        return {
            "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
            "concentration": [], "var_by_cohort": [],
            "correlations": None, "stress_results": [],
            "notes": [], "citations": [], "confidence": 0.85,
        }

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_analysts",
                           {"specialists": ["strategist", "risk"],
                            "brief": "Snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take",
                      {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tool_use("emit_section",
                      {"title": "Portfolio Snapshot",
                       "markdown": "- AAPL 100%", "citations": []}, "2"),
            _tool_use("emit_section",
                      {"title": "Risks", "markdown": "Concentrated.",
                       "citations": []}, "3"),
            _tool_use("emit_disclaimer", {}, "4"),
            _tool_use("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)
        mp.setattr(lb, "run_risk_manager", fake_risk)

        deltas: list[dict[str, Any]] = []
        async for d in lb.run_lead_banker(
            user_message="How is my portfolio doing?",
            resolution=None,
            client=fake_client,
            portfolio_mode=True,
            user_id="u-1",
        ):
            deltas.append(d)

    # Both specialists called exactly once
    assert len(captured_strategist) == 1
    assert len(captured_risk) == 1
    assert captured_strategist[0]["user_id"] == "u-1"
    assert captured_risk[0]["user_id"] == "u-1"
    # Synthesis stream contains the emitted sections
    section_titles = [d.get("title") for d in deltas if d.get("type") == "section"]
    assert "Portfolio Snapshot" in section_titles


@pytest.mark.asyncio
async def test_lead_banker_dispatch_subset_only_strategist() -> None:
    """If specialists=['strategist'], risk is NOT called."""
    captured_risk: list[dict[str, Any]] = []

    async def fake_strategist(**_kwargs: Any) -> dict[str, Any]:
        return {"portfolio_id": "p-1", "portfolio_name": "X", "cohorts": [],
                "rebalance": None, "notes": [], "citations": [], "confidence": 0.5}

    async def fake_risk(**kwargs: Any) -> dict[str, Any]:
        captured_risk.append(kwargs)
        return {}

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_analysts",
                           {"specialists": ["strategist"],
                            "brief": "Just snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(_tool_use("emit_done", {}, "1"), stop_reason="end_turn"),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)
        mp.setattr(lb, "run_risk_manager", fake_risk)

        async for _ in lb.run_lead_banker(
            user_message="Just my portfolio snapshot",
            resolution=None, client=fake_client,
            portfolio_mode=True, user_id="u-1",
        ):
            pass

    assert captured_risk == []
