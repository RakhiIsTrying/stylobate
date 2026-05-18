from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(
        input_tokens=10, output_tokens=20,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return m


def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


@pytest.mark.asyncio
async def test_chat_stream_end_to_end_screener(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    """User asks 'AI infrastructure plays in India' → screener fires → SSE sections."""

    # Fake universe loader — returns Nifty 500 constituents
    fake_nifty500 = [
        {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
        {"ticker": "PERSISTENT.NS", "name": "Persistent Systems", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
        {"ticker": "MPHASIS.NS", "name": "Mphasis", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
    ]

    # Scripted Sonnet responses for the screener agent
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _msg(_tu("resolve_universe", {"universes": ["nifty500"]}, "r1")),
        _msg(_tu("theme_to_universe", {
            "theme": "AI infrastructure",
            "tickers": ["TATAELXSI.NS", "PERSISTENT.NS", "MPHASIS.NS"],
        }, "t1")),
        _msg(
            _tu("submit_screener_findings", {
                "theme": "AI infrastructure plays in India",
                "universes_used": ["nifty500"],
                "candidates": [
                    {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi",
                     "market": "IN", "currency": "INR", "sector": "IT",
                     "current_price": 7150.0, "market_cap": 4.5e11,
                     "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5},
                    {"ticker": "PERSISTENT.NS", "name": "Persistent Systems",
                     "market": "IN", "currency": "INR", "sector": "IT",
                     "current_price": 5820.0, "market_cap": 8.8e11,
                     "pe_ttm": 51.8, "roe_pct": 28.7, "revenue_growth_yoy": 17.0},
                ],
                "filters_applied": {},
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_nifty500)), \
         patch("app.routes.chat.get_client", return_value=fake_client), \
         patch("app.agents.screener.get_client", return_value=fake_client):
        r = await client.post(
            "/chat/stream",
            json={"content": "AI infrastructure plays in India"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200
    body = r.text
    assert "Theme" in body or "AI infrastructure" in body
    assert "Candidates" in body
    assert "TATAELXSI.NS" in body
    assert "PERSISTENT.NS" in body
    assert "event: done" in body
