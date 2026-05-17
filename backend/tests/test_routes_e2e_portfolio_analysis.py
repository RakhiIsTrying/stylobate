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


def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


@pytest.mark.asyncio
async def test_chat_stream_portfolio_end_to_end_with_risk(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """Portfolio query → LB → dispatch_portfolio_analysts → both specialists → emit.

    Note: deviates from plan's separate-`get_client`-per-agent patches because
    lead_banker forwards its own `client` to the sub-agents (so module-level
    `get_client` patches are bypassed). Instead, we monkeypatch
    `run_portfolio_strategist` and `run_risk_manager` directly at the
    lead_banker module — the same pattern used in
    `test_lead_banker_portfolio_mode.py`. The LB mock supplies the two LB
    turns (dispatch + emit_*).
    """
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
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    # Lead Banker mock: turn 1 dispatches both analysts; turn 2 emits sections.
    lb_client = MagicMock()
    lb_client.messages.create = AsyncMock(side_effect=[
        _msg(_tu("dispatch_portfolio_analysts",
                 {"specialists": ["strategist", "risk"], "brief": "Snapshot"},
                 "d1"), stop_reason="tool_use"),
        _msg(
            _tu("emit_quick_take",
                {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tu("emit_section", {"title": "Portfolio Snapshot",
                                  "markdown": "USD: $10k +33%", "citations": []}, "2"),
            _tu("emit_section", {"title": "Concentration",
                                  "markdown": "AAPL 100% — critical",
                                  "citations": []}, "3"),
            _tu("emit_section", {"title": "Risks", "markdown": "concentrated.",
                                  "citations": []}, "4"),
            _tu("emit_disclaimer", {}, "5"),
            _tu("emit_done", {}, "6"),
            stop_reason="end_turn",
        ),
    ])

    # Canned sub-agent findings — replace the real Sonnet loops entirely.
    async def fake_strategist(**_kwargs: Any) -> dict[str, Any]:
        return {
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
        }

    async def fake_risk(**_kwargs: Any) -> dict[str, Any]:
        return {
            "portfolio_id": PID,
            "portfolio_name": "Test",
            "concentration": [{"position_id": None, "ticker": "AAPL",
                               "weight_pct": 1.0, "threshold_pct": 0.10,
                               "severity": "critical"}],
            "var_by_cohort": [],
            "correlations": None,
            "stress_results": [],
            "notes": [], "citations": [], "confidence": 0.85,
        }

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)
        mp.setattr(lb, "run_risk_manager", fake_risk)
        with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
             patch("app.tools.portfolio.get_prices",
                   AsyncMock(return_value=fake_prices)), \
             patch("app.tools.portfolio._fetch_price_history_for_position",
                   AsyncMock(return_value=[])), \
             patch("app.tools.risk._fetch_price_history",
                   AsyncMock(return_value=[])), \
             patch("app.data.benchmarks.get_benchmark_history",
                   AsyncMock(return_value=[])), \
             patch("app.tools.risk.get_usdinr",
                   AsyncMock(return_value=None)), \
             patch("app.routes.chat.get_client", return_value=lb_client):
            r = await client.post(
                "/chat/stream",
                json={"content": "How is my portfolio doing?"},
                headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
            )

    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "Concentration" in body
    assert "event: done" in body
