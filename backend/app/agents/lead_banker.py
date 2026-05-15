# backend/app/agents/lead_banker.py
import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from app.agents.fundamental import run_fundamental_analysis
from app.agents.technical import run_technical_analysis
from app.agents.ticker_resolver import TickerResolution
from app.core.anthropic_client import get_client

_MODEL = "claude-opus-4-7"
_MAX_TURNS = 12

_DISCLAIMER_TEXT = (
    "Educational analysis, not personalized investment advice. "
    "Do your own diligence and consider your tax situation."
)

_DISPATCH_TOOL: dict[str, Any] = {
    "name": "dispatch_specialists",
    "description": (
        "Run one or more specialist agents in parallel and receive their findings. "
        "Specialists available in Phase 2A: 'fundamental', 'technical'. "
        "Returns combined findings JSON as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {"type": "string", "enum": ["fundamental", "technical"]},
                "minItems": 1,
            },
            "brief": {"type": "string", "description": "One-line context for the specialists"},
        },
        "required": ["specialists", "brief"],
    },
}

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
        "description": "Stream a section of the research note with markdown and citations.",
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


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "lead_banker.md").read_text(encoding="utf-8")


def _build_user_message(user_text: str, resolution: TickerResolution) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, {resolution.market})\n\n"
        "Specialists available: fundamental, technical.\n\n"
        "Call dispatch_specialists first with the specialists you want, then use the emit_* tools "
        "to stream the response. Emit order: quick_take → stock_card → sections → recommendation "
        "→ disclaimer → done."
    )


async def _run_dispatch(specialists: list[str], brief: str, ticker: str) -> dict[str, Any]:
    """Run the requested specialists in parallel. Returns {name: findings_dict} + errors."""
    name_to_coro: dict[str, Any] = {}
    if "fundamental" in specialists:
        name_to_coro["fundamental"] = run_fundamental_analysis(ticker=ticker, brief=brief)
    if "technical" in specialists:
        name_to_coro["technical"] = run_technical_analysis(ticker=ticker, brief=brief)

    if not name_to_coro:
        return {"errors": {"none": "no recognized specialists requested"}}

    results = await asyncio.gather(*name_to_coro.values(), return_exceptions=True)
    out: dict[str, Any] = {"errors": {}}
    for name, res in zip(name_to_coro.keys(), results, strict=True):
        if isinstance(res, Exception):
            out["errors"][name] = str(res)
        else:
            out[name] = res.model_dump(mode="json")  # type: ignore[union-attr]
    return out


def _process_emit_block(block: Any) -> dict[str, Any] | None:
    if getattr(block, "type", None) != "tool_use":
        return None
    name = getattr(block, "name", "")
    args = cast(dict[str, Any], getattr(block, "input", {})) or {}
    if name == "emit_quick_take":
        return {"type": "quick_take", **args}
    if name == "emit_stock_card":
        return {"type": "stock_card", **args}
    if name == "emit_section":
        return {"type": "section", **args}
    if name == "emit_recommendation":
        return {"type": "recommendation", **args}
    if name == "emit_disclaimer":
        return {"type": "disclaimer", "text": _DISCLAIMER_TEXT}
    if name == "emit_done":
        return {"type": "done"}
    return None


async def run_lead_banker(
    *,
    user_message: str,
    resolution: TickerResolution,
    client: AsyncAnthropic | Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_user_message(user_message, resolution)},
    ]

    for _turn in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 4000,
            "system": system_blocks,
            "tools": [_DISPATCH_TOOL, *_EMIT_TOOLS],
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_use_blocks: list[Any] = []
        tool_results: list[dict[str, Any]] = []
        done = False

        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_use_blocks.append(block)
            name = getattr(block, "name", "")
            args = cast(dict[str, Any], getattr(block, "input", {})) or {}
            block_id = cast(str, getattr(block, "id", ""))

            if name == "dispatch_specialists":
                specialists = cast(list[str], args.get("specialists", []))
                brief = cast(str, args.get("brief", ""))
                findings = await _run_dispatch(specialists, brief, resolution.ticker)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(findings, default=str),
                })
                continue

            # Emit tool — yield delta and acknowledge to the model
            delta = _process_emit_block(block)
            if delta is not None:
                if delta["type"] == "done":
                    done = True
                yield delta
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block_id,
                "content": "ok",
            })

        if done or resp.stop_reason == "end_turn":
            return
        if not tool_use_blocks:
            return

        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})
