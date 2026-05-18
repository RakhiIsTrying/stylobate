from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolUseBlock

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.screener import build_screener_tools

_SUBMIT_TOOL = Tool(
    name="submit_screener_findings",
    description="Submit the final screener output. Call exactly once at the end.",
    input_schema={
        "type": "object",
        "properties": {
            "theme": {"type": "string"},
            "universes_used": {"type": "array", "items": {"type": "string"}},
            "candidates": {"type": "array"},
            "filters_applied": {"type": "object"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": [
            "theme", "universes_used", "candidates",
            "notes", "confidence",
        ],
    },
    impl=None,  # type: ignore[arg-type]  # marker tool; handled inline
)


def _load_system_prompt() -> str:
    return (
        Path(__file__).parent.parent / "prompts" / "screener.md"
    ).read_text(encoding="utf-8")


async def run_screener(
    *,
    user_message: str,
    user_id: str | None = None,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """Run the Screener Sonnet agent. Returns a findings dict."""
    c = client or get_client()
    tools = tools_override or build_screener_tools(user_id=user_id)
    all_tools = [*tools, _SUBMIT_TOOL]
    tool_schemas: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in all_tools
    ]
    tools_by_name = {t.name: t for t in tools}

    user_content = f"User query: {user_message}\n\nUse the tools per the prompt."
    messages: list[MessageParam] = [{"role": "user", "content": user_content}]

    findings: dict[str, Any] | None = None
    for _turn in range(8):
        resp = await c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_load_system_prompt(),
            tools=tool_schemas,  # type: ignore[arg-type]
            messages=messages,
        )
        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype != "tool_use":
                    continue
                name: str = str(block.get("name", ""))
                block_id: str = str(block.get("id", ""))
                args: dict[str, Any] = cast(dict[str, Any], block.get("input", {})) or {}
            else:
                btype = getattr(block, "type", None)
                if btype != "tool_use":
                    continue
                tu = cast(ToolUseBlock, block)
                name = tu.name
                block_id = tu.id
                args = cast(dict[str, Any], tu.input) or {}
            if name == "submit_screener_findings":
                findings = dict(args)
                break
            if name in tools_by_name:
                impl = tools_by_name[name].impl
                assert impl is not None
                result = await impl(**args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(result, default=str),
                })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                    "is_error": True,
                })
        if findings is not None:
            break
        if not tool_results:
            break
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})  # type: ignore[typeddict-item]

    if findings is None:
        findings = {
            "theme": user_message,
            "universes_used": [],
            "candidates": [],
            "filters_applied": {},
            "notes": ["Agent did not submit findings within turn budget."],
            "citations": [],
            "confidence": 0.0,
        }
    return findings
