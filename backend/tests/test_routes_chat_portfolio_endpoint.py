from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_chat_portfolio_streams_strategist_and_risk_sections(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    fake_strategist = {
        "portfolio_id": "00000000-0000-0000-0000-000000000001",
        "portfolio_name": "My Portfolio",
        "cohorts": [
            {"cohort": ["USD", "equity_etf"], "positions_count": 1,
             "total_value_native": 2000.0, "total_cost_native": 1500.0,
             "gain_pct": 33.3, "weights": {"AAPL": 1.0},
             "returns_1mo": 5.0, "returns_3mo": 12.0, "returns_1y": 30.0,
             "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 18.0,
             "sharpe_1y": 1.2, "beta_1y": 1.05, "max_drawdown_1y": -0.08,
             "prices_partial": False},
        ],
        "rebalance": None, "notes": [],
        "citations": [{"source": "yfinance", "ref": "AAPL 1y"}],
        "confidence": 0.85,
    }
    fake_risk = {
        "portfolio_id": "00000000-0000-0000-0000-000000000001",
        "portfolio_name": "My Portfolio",
        "concentration": [
            {"position_id": "p1", "ticker": "AAPL",
             "weight_pct": 1.0, "threshold_pct": 0.10, "severity": "critical"},
        ],
        "var_by_cohort": [
            {"cohort": ["USD", "equity_etf"], "confidence": 0.95,
             "horizon_days": 10, "methodology": "historical",
             "var_pct": -0.07, "var_native": -140.0,
             "insufficient_history": False},
        ],
        "correlations": {"tickers": ["AAPL"], "matrix": [[1.0]], "excluded": []},
        "stress_results": [
            {"scenario": "equity_-20%",
             "per_position": [{"ticker": "AAPL", "before_native": 2000.0,
                               "after_native": 1600.0, "delta_native": -400.0,
                               "delta_pct": -20.0}],
             "by_cohort": [],
             "total_delta_usd": -400.0, "assumed_fx": {}},
        ],
        "notes": [], "citations": [], "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_strategist),
    ), patch(
        "app.routes.chat_portfolio.run_risk_manager",
        AsyncMock(return_value=fake_risk),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "Full analysis"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "Portfolio Snapshot" in body
    assert "Concentration" in body
    assert "Value at Risk" in body
    assert "Stress Tests" in body
    assert "AAPL" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_handles_specialist_failure(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """If Risk Manager fails, strategist's sections still ship + an error note appears."""
    fake_strategist: dict[str, Any] = {
        "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
        "cohorts": [
            {"cohort": ["USD", "equity_etf"], "positions_count": 1,
             "total_value_native": 1000.0, "total_cost_native": 1000.0,
             "gain_pct": 0.0, "weights": {"X": 1.0},
             "returns_1mo": 0.0, "returns_3mo": 0.0, "returns_1y": 0.0,
             "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 0.0,
             "sharpe_1y": None, "beta_1y": None, "max_drawdown_1y": None,
             "prices_partial": False},
        ],
        "rebalance": None, "notes": [], "citations": [], "confidence": 0.7,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_strategist),
    ), patch(
        "app.routes.chat_portfolio.run_risk_manager",
        AsyncMock(side_effect=RuntimeError("risk module failed")),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "Full analysis"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "risk module failed" in body or "Risk analysis unavailable" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/portfolio", json={"message": "hi"})
    assert r.status_code == 401
