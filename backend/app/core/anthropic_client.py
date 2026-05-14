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
    Each element of `system_blocks` must have a `"text"` key. Blocks are
    emitted in order with `cache_control: ephemeral` so the Anthropic API
    caches them with a 5-minute TTL.
    """
    sys_blocks = [
        {"type": "text", "text": b["text"], "cache_control": {"type": "ephemeral"}}
        for b in system_blocks
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": sys_blocks,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools

    resp = await client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return AgentReply(text=text, raw=resp, usage=resp.usage, stop_reason=resp.stop_reason)
