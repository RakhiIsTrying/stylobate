from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.routes.chat import is_portfolio_query

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_is_portfolio_query_matches_common_phrasings() -> None:
    assert is_portfolio_query("how is my portfolio doing?")
    assert is_portfolio_query("rebalance my holdings")
    assert is_portfolio_query("review my allocation please")
    assert is_portfolio_query("am I too concentrated?")
    assert is_portfolio_query("how am I doing on investments")
    assert not is_portfolio_query("what's AAPL doing today")
    assert not is_portfolio_query("Is Tesla a buy?")


@pytest.mark.asyncio
async def test_chat_stream_portfolio_query_skips_ticker_resolution(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """Portfolio query must skip resolve_ticker and run Lead Banker in portfolio_mode."""
    resolve_mock = AsyncMock()  # should not be called

    async def fake_lead_banker(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "progress", "step": "running_portfolio_strategist"}
        yield {"type": "section", "title": "Portfolio Snapshot",
               "markdown": "- USD equity 100%", "citations": []}
        yield {"type": "done"}

    with patch("app.routes.chat.resolve_ticker", resolve_mock), \
         patch("app.routes.chat.run_lead_banker", fake_lead_banker):
        r = await client.post(
            "/chat/stream",
            json={"content": "How is my portfolio doing?"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    resolve_mock.assert_not_called()
    assert "Portfolio Snapshot" in body
