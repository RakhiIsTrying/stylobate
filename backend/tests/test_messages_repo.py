# backend/tests/test_messages_repo.py
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_insert_user_message_returns_row() -> None:
    fake_client: MagicMock = MagicMock()
    inserted_row = {
        "id": str(uuid4()),
        "chat_id": str(uuid4()),
        "role": "user",
        "content": {"type": "text", "text": "hi"},
        "created_at": "2026-05-14T18:00:00+00:00",
    }
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

    from app.db.messages import insert_message
    msg = await insert_message(
        fake_client,
        chat_id=str(inserted_row["chat_id"]),
        role="user",
        content={"type": "text", "text": "hi"},
    )
    assert str(msg.id) == inserted_row["id"]
    assert msg.role == "user"
    assert msg.content == {"type": "text", "text": "hi"}


@pytest.mark.asyncio
async def test_get_or_create_chat_creates_when_missing() -> None:
    fake_client: MagicMock = MagicMock()
    # First call: select returns empty
    select_q = fake_client.table.return_value.select.return_value
    select_q.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    # Second call: insert returns one row
    chat_id = str(uuid4())
    user_id = str(uuid4())
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": chat_id,
        "user_id": user_id,
        "title": None,
        "model": "claude-opus-4-7",
        "created_at": "2026-05-14T18:00:00+00:00",
        "last_message_at": "2026-05-14T18:00:00+00:00",
    }]

    from app.db.messages import get_or_create_chat
    chat = await get_or_create_chat(fake_client, user_id=user_id, chat_id=None)
    assert str(chat.user_id) == user_id
    assert chat.title is None
