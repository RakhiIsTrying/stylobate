from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolUseBlock

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.portfolio import build_portfolio_tools

_SUBMIT_TOOL = Tool(
    name="submit_portfolio_findings",
    description="Submit the final portfolio analysis. Call exactly once at the end.",
    input_schema={
        "type": "object",
        "properties": {
            "portfolio_id": {"type": ["string", "null"]},
            "portfolio_name": {"type": "string"},
            "cohorts": {"type": "array"},
            "rebalance": {"type": ["object", "null"]},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": ["portfolio_id", "portfolio_name", "cohorts", "notes", "confidence"],
    },
    impl=None,  # type: ignore[arg-type]  # marker tool; handled inline
)


def _load_system_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "portfolio_strategist.md"
    return path.read_text(encoding="utf-8")


async def run_portfolio_strategist(
    *,
    user_id: str,
    portfolio_id: str | None,
    brief: str,
    target_alloc: dict[str, float] | None = None,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """Run the Portfolio Strategist Sonnet agent. Returns a findings dict.

    user_id is closure-scoped into the tools — never passed to the LLM.
    """
    c = client or get_client()
    tools = tools_override or build_portfolio_tools(user_id=user_id, portfolio_id=portfolio_id)
    # Append the submit tool
    all_tools = [*tools, _SUBMIT_TOOL]
    tool_schemas: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in all_tools
    ]
    tools_by_name = {t.name: t for t in tools}

    user_content = (
        f"Brief: {brief}\n"
        f"Portfolio ID: {portfolio_id or '(default: first)'}\n"
    )
    if target_alloc:
        user_content += f"target_alloc: {json.dumps(target_alloc)}\n"
    user_content += (
        "\nUse the tools to read holdings, compute stats, optionally suggest "
        "rebalance, then submit_portfolio_findings."
    )

    messages: list[MessageParam] = [{"role": "user", "content": user_content}]

    findings: dict[str, Any] | None = None
    for _turn in range(8):  # bounded loop
        resp = await c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
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
                name: str = block["name"]
                block_id: str = block["id"]
                args: dict[str, Any] = block["input"]
            else:
                btype = getattr(block, "type", None)
                if btype != "tool_use":
                    continue
                tu = cast(ToolUseBlock, block)
                name = tu.name
                block_id = tu.id
                args = cast(dict[str, Any], tu.input)
            if name == "submit_portfolio_findings":
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
            "portfolio_id": portfolio_id,
            "portfolio_name": "",
            "cohorts": [],
            "rebalance": None,
            "notes": ["Agent did not submit findings within turn budget."],
            "citations": [],
            "confidence": 0.0,
        }
    return findings
