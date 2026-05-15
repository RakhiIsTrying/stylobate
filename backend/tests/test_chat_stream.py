# backend/tests/test_chat_stream.py
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.agents.ticker_resolver import TickerResolution
from app.db.models import Chat, Message


async def _fake_lead_banker_stream() -> AsyncIterator[dict[str, Any]]:
    deltas: list[dict[str, Any]] = [
        {"type": "quick_take", "signal": "tactical_buy", "qualifier": "trend up"},
        {"type": "stock_card", "ticker": "AAPL", "name": "Apple Inc.",
         "market": "US", "currency": "USD", "stats": {"RSI": "62"}},
        {"type": "section", "title": "Thesis", "markdown": "Strong.", "citations": []},
        {"type": "section", "title": "Fundamentals",
         "markdown": "Net margin 25.5% [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}]},
        {"type": "section", "title": "Technicals",
         "markdown": "RSI 62 [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:indicators:AAPL", "index": 1}]},
        {"type": "section", "title": "Risks", "markdown": "Macro.", "citations": []},
        {"type": "recommendation", "signal": "tactical_buy",
         "position_size_range": [2, 4], "entry_zone": "210-220",
         "stop": "200", "target_12mo_base": "260"},
        {"type": "disclaimer", "text": "Educational analysis…"},
        {"type": "done"},
    ]
    for d in deltas:
        yield d


@pytest.mark.asyncio
async def test_chat_stream_rejects_unauth(client: AsyncClient) -> None:
    response = await client.post("/chat/stream", json={"content": "deep dive AAPL"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_stream_full_pipeline(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()
    fake_chat = Chat(
        id=chat_id, user_id=uuid.UUID(user_id), title=None,
        model="claude-opus-4-7",
        created_at="2026-05-15T10:00:00+00:00",
        last_message_at="2026-05-15T10:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "deep dive AAPL"},
        created_at="2026-05-15T10:00:00+00:00",
    )
    fake_asst_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="assistant",
        content=[{"type": "done"}],
        created_at="2026-05-15T10:00:02+00:00",
    )
    fake_resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message",
              new=AsyncMock(side_effect=[fake_user_msg, fake_asst_msg])),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=fake_resolution)),
        patch("app.routes.chat.run_lead_banker", return_value=_fake_lead_banker_stream()),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "deep dive AAPL"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: progress" in body
    assert "resolving_ticker" in body
    assert "running_specialists" in body
    assert "event: delta" in body
    assert '"type": "quick_take"' in body
    assert '"type": "stock_card"' in body
    assert '"title": "Fundamentals"' in body
    assert '"title": "Technicals"' in body
    assert '"type": "recommendation"' in body
    assert '"type": "disclaimer"' in body
    assert body.count("event: done") == 1


@pytest.mark.asyncio
async def test_chat_stream_rejects_low_confidence_resolution(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()
    fake_chat = Chat(
        id=chat_id, user_id=uuid.UUID(user_id), title=None,
        model="claude-opus-4-7",
        created_at="2026-05-15T10:00:00+00:00",
        last_message_at="2026-05-15T10:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "asdfqwer"},
        created_at="2026-05-15T10:00:00+00:00",
    )
    low = TickerResolution(
        ticker="", name="", market="US", asset_class="equity", confidence=0.0,
    )
    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message", new=AsyncMock(return_value=fake_user_msg)),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=low)),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "asdfqwer"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )
    body = response.text
    assert response.status_code == 200
    assert "event: error" in body
    assert "couldn't identify the ticker" in body
    assert "event: done" in body
