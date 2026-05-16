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
async def test_chat_portfolio_streams_sections(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    fake_findings: dict[str, Any] = {
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
        "rebalance": None,
        "notes": [],
        "citations": [{"source": "yfinance", "ref": "AAPL 1y"}],
        "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_findings),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "How am I doing?"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "event: delta" in body
    assert "Portfolio Snapshot" in body or "snapshot" in body.lower()
    assert "AAPL" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/portfolio", json={"message": "hi"})
    assert r.status_code == 401
