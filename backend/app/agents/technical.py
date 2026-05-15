# backend/app/agents/technical.py
import json
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.agents.fundamental import Citation
from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.technical import (
    calc_indicators_tool,
    detect_patterns_tool,
    get_price_history_tool,
    get_volume_profile_tool,
)

_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 8


class TechnicalFinding(BaseModel):
    ticker: str
    trend: Literal["uptrend", "downtrend", "sideways"]
    rsi_14: float | None = None
    macd_signal: Literal["bullish", "bearish", "neutral"] | None = None
    key_levels: list[float] = []
    pattern_notes: list[str] = []
    citations: list[Citation] = []
    confidence: float


class TechnicalError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_technical_findings",
    "description": "Emit final TechnicalFinding and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "trend": {"type": "string", "enum": ["uptrend", "downtrend", "sideways"]},
            "rsi_14": {"type": "number"},
            "macd_signal": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "key_levels": {"type": "array", "items": {"type": "number"}},
            "pattern_notes": {"type": "array", "items": {"type": "string"}},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "ref": {"type": "string"},
                        "snippet": {"type": "string"},
                    },
                    "required": ["source", "ref"],
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["ticker", "trend", "confidence"],
    },
}


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "technical.md").read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_price_history_tool.name: get_price_history_tool,
    calc_indicators_tool.name: calc_indicators_tool,
    detect_patterns_tool.name: detect_patterns_tool,
    get_volume_profile_tool.name: get_volume_profile_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_technical_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",
    client: AsyncAnthropic | Any | None = None,
) -> TechnicalFinding:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Ticker: {ticker} (market: {market})\nBrief: {brief}\n"
                f"Use the technical tools (pass market=\"{market}\" on every call) "
                "and submit_technical_findings when ready."
            ),
        }
    ]

    for _ in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 1500,
            "temperature": 0.3,
            "system": system_blocks,
            "tools": _all_tool_schemas(),
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            raise TechnicalError("technical agent emitted no tool calls")

        messages.append({"role": "assistant", "content": resp.content})

        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = cast(str, getattr(tu, "name", ""))
            tu_id = cast(str, getattr(tu, "id", ""))
            tu_input = cast(dict[str, Any], getattr(tu, "input", {})) or {}
            if name == "submit_technical_findings":
                return TechnicalFinding(**tu_input)
            tool = _TOOLS_BY_NAME.get(name)
            if tool is None:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                    "is_error": True,
                })
                continue
            try:
                payload = await tool.impl(**tu_input)
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": payload.model_dump_json(),
                })
            except Exception as e:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results_content})

    raise TechnicalError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit")
