# backend/app/agents/ticker_resolver.py
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.core.anthropic_client import get_client


class TickerResolution(BaseModel):
    ticker: str
    name: str
    market: Literal["US", "IN", "CRYPTO"]
    asset_class: Literal["equity", "etf", "crypto"]
    confidence: float
    candidates: list[str] = []


_RESOLVE_TOOL: dict[str, Any] = {
    "name": "resolve",
    "description": "Emit the resolved ticker for the user's input. Call this exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "name": {"type": "string"},
            "market": {"type": "string", "enum": ["US", "IN", "CRYPTO"]},
            "asset_class": {"type": "string", "enum": ["equity", "etf", "crypto"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "candidates": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ticker", "name", "market", "asset_class", "confidence"],
    },
}


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "ticker_resolver.md"
    return path.read_text(encoding="utf-8")


async def resolve_ticker(
    user_text: str,
    *,
    client: AsyncAnthropic | Any | None = None,
) -> TickerResolution:
    c = client or get_client()
    sys = _load_prompt()
    kwargs: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "max_tokens": 200,
        "temperature": 0.0,
        "system": [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}],
        "tools": [_RESOLVE_TOOL],
        "tool_choice": {"type": "tool", "name": "resolve"},
        "messages": [{"role": "user", "content": user_text}],
    }
    resp = await c.messages.create(**kwargs)
    # Pull the tool_use block — there must be exactly one with name "resolve".
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "resolve":
            args = cast(dict[str, Any], block.input)
            args.setdefault("candidates", [])
            return TickerResolution(**args)
    raise RuntimeError("ticker_resolver: no resolve tool call in response")
