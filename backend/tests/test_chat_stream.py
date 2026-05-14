# backend/tests/test_chat_stream.py
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.db.models import Chat, Message


@pytest.mark.asyncio
async def test_chat_stream_rejects_unauth(client: AsyncClient) -> None:
    response = await client.post("/chat/stream", json={"content": "hi"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_stream_echoes_message(
    client: AsyncClient,
    make_token: Callable[..., str],
) -> None:
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()

    fake_chat = Chat(
        id=chat_id,
        user_id=uuid.UUID(user_id),
        title=None,
        model="claude-opus-4-7",
        created_at="2026-05-14T18:00:00+00:00",
        last_message_at="2026-05-14T18:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(),
        chat_id=chat_id,
        role="user",
        content={"type": "text", "text": "hello"},
        created_at="2026-05-14T18:00:00+00:00",
    )
    fake_asst_msg = Message(
        id=uuid.uuid4(),
        chat_id=chat_id,
        role="assistant",
        content=[{"type": "text", "text": "echo: hello"}],
        created_at="2026-05-14T18:00:01+00:00",
    )

    mock_insert = AsyncMock(side_effect=[fake_user_msg, fake_asst_msg])
    with patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)), \
         patch("app.routes.chat.insert_message", new=mock_insert), \
         patch("app.routes.chat.get_user_client", return_value=object()):
        response = await client.post(
            "/chat/stream",
            json={"content": "hello"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: delta" in body
    assert "echo: hello" in body
    assert "event: done" in body
