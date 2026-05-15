# backend/app/agents/lead_banker.py
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from app.agents.fundamental import FundamentalFindings
from app.agents.ticker_resolver import TickerResolution
from app.core.anthropic_client import get_client

_MODEL = "claude-opus-4-7"

_MAX_TURNS = 12  # safety cap — 6 emit_* tools, each needs one round-trip at most

_EMIT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "emit_quick_take",
        "description": "Stream a single-line quick take.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal": {
                    "type": "string",
                    "enum": ["tactical_buy", "accumulate", "hold", "reduce"],
                },
                "qualifier": {"type": "string"},
            },
            "required": ["signal", "qualifier"],
        },
    },
    {
        "name": "emit_stock_card",
        "description": "Stream the stock identity + price + key stats card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "name": {"type": "string"},
                "market": {"type": "string"},
                "currency": {"type": "string"},
                "stats": {"type": "object"},
            },
            "required": ["ticker", "name", "market", "currency", "stats"],
        },
    },
    {
        "name": "emit_section",
        "description": "Stream a section of the research note with markdown and citation list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "markdown": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "ref": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                        "required": ["source", "ref"],
                    },
                },
            },
            "required": ["title", "markdown", "citations"],
        },
    },
    {
        "name": "emit_recommendation",
        "description": "Stream the recommendation card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal": {
                    "type": "string",
                    "enum": ["tactical_buy", "accumulate", "hold", "reduce"],
                },
                "position_size_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "entry_zone": {"type": "string"},
                "stop": {"type": "string"},
                "target_12mo_base": {"type": "string"},
            },
            "required": ["signal", "position_size_range", "entry_zone", "stop", "target_12mo_base"],
        },
    },
    {
        "name": "emit_disclaimer",
        "description": "Stream the educational disclaimer. No arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "emit_done",
        "description": "Signal the end of streaming. No arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_DISCLAIMER_TEXT = (
    "Educational analysis, not personalized investment advice. "
    "Do your own diligence and consider your tax situation."
)


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "lead_banker.md"
    return path.read_text(encoding="utf-8")


def _build_user_message(
    user_text: str,
    resolution: TickerResolution,
    findings: FundamentalFindings,
) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, {resolution.market})\n\n"
        f"Fundamental Analyst findings:\n{findings.model_dump_json(indent=2)}\n\n"
        "Now synthesize the response by calling the emit_* tools in the prescribed order."
    )


def _process_block(
    block: Any,
) -> dict[str, Any] | None:
    """Convert a tool_use block into a delta dict, or return None if not a known tool."""
    if getattr(block, "type", None) != "tool_use":
        return None
    name = getattr(block, "name", "")
    args = cast(dict[str, Any], getattr(block, "input", {})) or {}
    if name == "emit_quick_take":
        return {"type": "quick_take", **args}
    elif name == "emit_stock_card":
        return {"type": "stock_card", **args}
    elif name == "emit_section":
        return {"type": "section", **args}
    elif name == "emit_recommendation":
        return {"type": "recommendation", **args}
    elif name == "emit_disclaimer":
        return {"type": "disclaimer", "text": _DISCLAIMER_TEXT}
    elif name == "emit_done":
        return {"type": "done"}
    return None


async def run_lead_banker(
    *,
    user_message: str,
    resolution: TickerResolution,
    fundamental_findings: FundamentalFindings,
    client: AsyncAnthropic | Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [
        {"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}},
    ]
    user_content = _build_user_message(user_message, resolution, fundamental_findings)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_content},
    ]

    for _turn in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 4000,
            "system": system_blocks,
            "tools": _EMIT_TOOLS,
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        # Collect tool_use blocks for reply and emit deltas
        tool_use_blocks: list[Any] = []
        tool_results: list[dict[str, Any]] = []
        done = False

        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_use_blocks.append(block)
            delta = _process_block(block)
            if delta is not None:
                if delta["type"] == "done":
                    done = True
                    yield delta
                else:
                    yield delta
            # Always send back a success result so the model can continue
            block_id = cast(str, getattr(block, "id", ""))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block_id,
                "content": "ok",
            })

        if done or resp.stop_reason == "end_turn":
            return

        if not tool_use_blocks:
            # Model returned without calling any tools — stop to avoid infinite loop
            return

        # Append assistant turn + tool results and loop
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})
