from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.data.prices_cache import Price

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.mark.asyncio
async def test_chat_stream_portfolio_end_to_end(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """Portfolio query → Lead Banker dispatch → strategist → emit sections."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 50, "cost_basis": 150.0,
         "currency": "USD", "opened_at": date(2024, 6, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    fake_prices: dict[tuple[str, str], Price] = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                              currency="USD", as_of=datetime(2026, 5, 15)),
    }

    # Mock the LLM responses for Lead Banker
    def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
        m = MagicMock()
        m.content = list(blocks)
        m.stop_reason = stop_reason
        m.usage = MagicMock(input_tokens=10, output_tokens=20,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return m

    def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
        return {"type": "tool_use", "id": tid, "name": name, "input": args}

    # Strategist sub-agent: get_holdings → submit
    strategist_responses = [
        _msg(_tu("get_holdings", {}, "g1")),
        _msg(_tu("submit_portfolio_findings", {
            "portfolio_id": PID,
            "portfolio_name": "Test",
            "cohorts": [{
                "cohort": ["USD", "equity_etf"], "positions_count": 1,
                "total_value_native": 10000.0, "total_cost_native": 7500.0,
                "gain_pct": 33.3, "weights": {"AAPL": 1.0},
                "returns_1mo": 5.0, "returns_3mo": 10.0, "returns_1y": 25.0,
                "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 15.0,
                "sharpe_1y": 1.1, "beta_1y": 1.0, "max_drawdown_1y": -0.10,
                "prices_partial": False,
            }],
            "rebalance": None, "notes": [], "citations": [],
            "confidence": 0.85,
        }, "s1"), stop_reason="tool_use"),
    ]
    # Lead Banker: dispatch_portfolio_strategist → emit_* → done
    lb_responses = [
        _msg(_tu("dispatch_portfolio_strategist",
                 {"brief": "Snapshot"}, "d1"), stop_reason="tool_use"),
        _msg(
            _tu("emit_quick_take", {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tu("emit_section", {"title": "Portfolio Snapshot",
                                  "markdown": "USD: $10k +33%", "citations": []}, "2"),
            _tu("emit_section", {"title": "Risks", "markdown": "concentrated.",
                                  "citations": []}, "3"),
            _tu("emit_disclaimer", {}, "4"),
            _tu("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ]
    # Call order: LB turn 1 (dispatch) → strategist turn 1 (get_holdings)
    # → strategist turn 2 (submit) → LB turn 2 (emit_*)
    all_responses = [
        lb_responses[0],
        strategist_responses[0],
        strategist_responses[1],
        lb_responses[1],
    ]

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=all_responses)

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.portfolio._fetch_price_history_for_position",
               AsyncMock(return_value=[])), \
         patch("app.data.benchmarks.get_benchmark_history",
               AsyncMock(return_value=[])), \
         patch("app.routes.chat.get_client", return_value=fake_client), \
         patch("app.agents.portfolio_strategist.get_client", return_value=fake_client):
        r = await client.post(
            "/chat/stream",
            json={"content": "How is my portfolio doing?"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "event: done" in body
