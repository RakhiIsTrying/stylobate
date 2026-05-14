# backend/app/routes/chat.py
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import get_current_token, get_current_user
from app.core.logging import get_logger
from app.core.supabase_client import get_user_client
from app.db.messages import get_or_create_chat, insert_message

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger(__name__)


class ChatStreamRequest(BaseModel):
    content: str
    chat_id: str | None = None


def _sse(event: str, data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/stream")
async def chat_stream(
    req: ChatStreamRequest,
    user: dict[str, Any] = Depends(get_current_user),
    token: str = Depends(get_current_token),
) -> StreamingResponse:
    user_id = user["sub"]
    sb = get_user_client(token)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        chat = await get_or_create_chat(sb, user_id=user_id, chat_id=req.chat_id)

        user_msg = await insert_message(
            sb, chat_id=chat.id, role="user",
            content={"type": "text", "text": req.content},
        )
        yield _sse("progress", {"step": "received", "message_id": str(user_msg.id)})

        echo = f"echo: {req.content}"
        yield _sse("delta", {"type": "text", "text": echo})

        asst_msg = await insert_message(
            sb, chat_id=chat.id, role="assistant",
            content=[{"type": "text", "text": echo}],
        )
        yield _sse("done", {"message_id": str(asst_msg.id), "chat_id": str(chat.id)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
