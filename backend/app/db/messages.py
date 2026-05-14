# backend/app/db/messages.py
from typing import Any, Literal, cast
from uuid import UUID

from supabase import Client

from app.db.models import Chat, Message


async def insert_message(
    client: Client,
    *,
    chat_id: str | UUID,
    role: Literal["user", "assistant", "system"],
    content: dict[str, Any] | list[Any],
) -> Message:
    resp = (
        client.table("messages")
        .insert({"chat_id": str(chat_id), "role": role, "content": content})
        .execute()
    )
    row = cast(dict[str, Any], resp.data[0])
    return Message(**row)


async def get_or_create_chat(
    client: Client,
    *,
    user_id: str | UUID,
    chat_id: str | UUID | None,
) -> Chat:
    if chat_id is not None:
        resp = (
            client.table("chats")
            .select("*")
            .eq("id", str(chat_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        if resp.data:
            return Chat(**cast(dict[str, Any], resp.data[0]))
    resp = client.table("chats").insert({"user_id": str(user_id)}).execute()
    return Chat(**cast(dict[str, Any], resp.data[0]))
