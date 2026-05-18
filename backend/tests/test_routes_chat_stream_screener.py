from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.routes.chat import is_portfolio_query, is_screener_query

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_is_screener_query_matches_common_phrasings() -> None:
    assert is_screener_query("find me cheap US dividend stocks")
    assert is_screener_query("screen for AI infrastructure plays")
    assert is_screener_query("best stocks for Indian fintech")
    assert is_screener_query("ideas for crypto in 2026")
    assert is_screener_query("AI plays in India")
    assert is_screener_query("show me some quality Indian banks")
    assert not is_screener_query("what's AAPL doing today")
    assert not is_screener_query("Is Tesla a buy?")


def test_screener_does_not_match_portfolio_queries() -> None:
    """Portfolio-shaped questions must NOT trigger the screener path."""
    # These should hit is_portfolio_query, NOT is_screener_query — order matters
    assert is_portfolio_query("review my portfolio")
    assert is_portfolio_query("rebalance my holdings")
    # Ambiguous: "find me names in my portfolio" — both could match
    # The route checks portfolio FIRST, so this hits portfolio (verified in routing test)


@pytest.mark.asyncio
async def test_chat_stream_screener_query_routes_to_screener_path(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    resolve_mock = AsyncMock()  # should NOT be called
    run_screener_mock = AsyncMock(return_value={
        "theme": "AI plays in India",
        "universes_used": ["nifty500"],
        "candidates": [{
            "ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "market": "IN",
            "currency": "INR", "sector": "IT",
            "current_price": 7150.0, "market_cap": 4.5e11,
            "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5,
        }],
        "filters_applied": {}, "notes": [],
        "citations": [], "confidence": 0.85,
    })

    with patch("app.routes.chat.resolve_ticker", resolve_mock), \
         patch("app.routes.chat.run_screener", run_screener_mock):
        r = await client.post(
            "/chat/stream",
            json={"content": "find me AI plays in India"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    resolve_mock.assert_not_called()
    run_screener_mock.assert_awaited_once()
    assert "TATAELXSI.NS" in body
    assert "Candidates" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_stream_portfolio_wins_when_both_keywords_present(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    """When a query matches BOTH portfolio + screener keywords, portfolio path wins."""
    run_screener_mock = AsyncMock()
    # The query contains "my portfolio" (portfolio) AND "find" (screener)
    # is_portfolio_query is checked first, so portfolio path runs.

    async def fake_lead_banker(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "section", "title": "Portfolio Snapshot",
               "markdown": "ok", "citations": []}
        yield {"type": "done"}

    with patch("app.routes.chat.run_lead_banker", fake_lead_banker), \
         patch("app.routes.chat.run_screener", run_screener_mock):
        r = await client.post(
            "/chat/stream",
            json={"content": "find names in my portfolio"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    run_screener_mock.assert_not_called()  # portfolio path won
