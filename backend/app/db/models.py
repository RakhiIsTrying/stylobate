# backend/app/db/models.py
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    chat_id: UUID
    role: Literal["user", "assistant", "system"]
    content: dict[str, Any] | list[Any]
    created_at: datetime


class Chat(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    user_id: UUID
    title: str | None = None
    model: str = "claude-opus-4-7"
    created_at: datetime
    last_message_at: datetime
