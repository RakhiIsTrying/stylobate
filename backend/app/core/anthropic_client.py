# backend/app/core/anthropic_client.py
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from app.config import get_settings


@lru_cache(maxsize=1)
def get_client() -> AsyncAnthropic:
    settings = get_settings()
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


@dataclass
class AgentReply:
    text: str
    raw: Message
    usage: Any        # anthropic Usage object
    stop_reason: str | None


async def call_with_cache(
    *,
    client: AsyncAnthropic | Any,
    model: str,
    system_blocks: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float = 0.3,
) -> AgentReply:
    """Call Anthropic Messages with the supplied system blocks marked for caching.

    `system_blocks` should be in the order [persona, tool_defs_summary, guidelines].
    Each block becomes `{"type": "text", "text": ..., "cache_control": ephemeral}`.
    """
    sys = [
        {"type": "text", "text": b["text"], "cache_control": {"type": "ephemeral"}}
        for b in system_blocks
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": sys,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools

    resp = await client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return AgentReply(text=text, raw=resp, usage=resp.usage, stop_reason=resp.stop_reason)
