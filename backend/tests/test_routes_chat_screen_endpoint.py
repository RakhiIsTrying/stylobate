from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_chat_screen_streams_sections(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    fake_findings = {
        "theme": "AI infrastructure plays in India",
        "universes_used": ["nifty500"],
        "candidates": [
            {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "market": "IN",
             "currency": "INR", "sector": "IT Services",
             "current_price": 7150.0, "market_cap": 450_000_000_000,
             "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5},
            {"ticker": "PERSISTENT.NS", "name": "Persistent Systems", "market": "IN",
             "currency": "INR", "sector": "IT Services",
             "current_price": 5820.0, "market_cap": 880_000_000_000,
             "pe_ttm": 51.8, "roe_pct": 28.7, "revenue_growth_yoy": 17.0},
        ],
        "filters_applied": {},
        "notes": [],
        "citations": [{"source": "yfinance", "ref": "live fundamentals"}],
        "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_screen.run_screener",
        AsyncMock(return_value=fake_findings),
    ):
        r = await client.post(
            "/chat/screen",
            json={"message": "AI infrastructure plays in India"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "Theme" in body or "AI infrastructure" in body
    assert "Candidates" in body
    assert "TATAELXSI.NS" in body
    assert "PERSISTENT.NS" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_screen_empty_candidates_section(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    fake = {
        "theme": "X", "universes_used": ["sp500"],
        "candidates": [], "filters_applied": {},
        "notes": ["Theme too broad — narrow with sectors or filters."],
        "citations": [], "confidence": 0.3,
    }
    with patch(
        "app.routes.chat_screen.run_screener", AsyncMock(return_value=fake),
    ):
        r = await client.post(
            "/chat/screen", json={"message": "anything"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "Theme too broad" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_screen_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/screen", json={"message": "hi"})
    assert r.status_code == 401
