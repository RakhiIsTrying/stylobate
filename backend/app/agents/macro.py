# backend/app/agents/macro.py
import json
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.agents.fundamental import Citation
from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.macro import (
    get_fred_series_tool,
    get_rates_tool,
    get_sector_perf_tool,
)

_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 6


class MacroFindings(BaseModel):
    regime: Literal["expansionary", "neutral", "tightening", "uncertain"]
    rates_snapshot: dict[str, float | None] = {}
    sector_performance: dict[str, float] = {}
    macro_notes: list[str] = []
    citations: list[Citation] = []
    confidence: float


class MacroError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_macro_findings",
    "description": "Emit final MacroFindings and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {
                "type": "string",
                "enum": ["expansionary", "neutral", "tightening", "uncertain"],
            },
            "rates_snapshot": {
                "type": "object",
                "additionalProperties": {"type": ["number", "null"]},
            },
            "sector_performance": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
            "macro_notes": {"type": "array", "items": {"type": "string"}},
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
        "required": ["regime", "confidence"],
    },
}


def _load_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "macro.md"
    return prompt_path.read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_rates_tool.name: get_rates_tool,
    get_sector_perf_tool.name: get_sector_perf_tool,
    get_fred_series_tool.name: get_fred_series_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_macro_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> MacroFindings:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"User question relates to ticker: {ticker}\nBrief: {brief}\n"
                "Use the macro tools and submit_macro_findings when ready."
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
            raise MacroError("macro agent emitted no tool calls")

        messages.append({"role": "assistant", "content": resp.content})

        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = cast(str, getattr(tu, "name", ""))
            tu_id = cast(str, getattr(tu, "id", ""))
            tu_input = cast(dict[str, Any], getattr(tu, "input", {})) or {}
            if name == "submit_macro_findings":
                return MacroFindings(**tu_input)
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

    raise MacroError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit")
