# backend/app/agents/fundamental.py
import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import ToolUseBlock
from pydantic import BaseModel

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.filings import get_filings_tool
from app.tools.financials import (
    get_financials_tool,
    get_key_ratios_tool,
)

_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 8


class Citation(BaseModel):
    source: str
    ref: str
    snippet: str | None = None


class FundamentalFindings(BaseModel):
    ticker: str
    thesis: str
    fundamentals_summary: list[str]
    risks: list[str]
    citations: list[Citation]
    confidence: float


class FundamentalError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_findings",
    "description": "Emit the final FundamentalFindings and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "thesis": {"type": "string"},
            "fundamentals_summary": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
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
        "required": [
            "ticker", "thesis", "fundamentals_summary", "risks", "citations", "confidence",
        ],
    },
}


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "fundamental.md"
    return path.read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_key_ratios_tool.name: get_key_ratios_tool,
    get_financials_tool.name: get_financials_tool,
    get_filings_tool.name: get_filings_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_fundamental_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",
    client: AsyncAnthropic | Any | None = None,
) -> FundamentalFindings:
    if market == "CRYPTO":
        return FundamentalFindings(
            ticker=ticker,
            thesis=(
                "Crypto assets do not have traditional financial statements. "
                "Refer to the Technical, News, and Macro sections for context."
            ),
            fundamentals_summary=[
                "Not applicable: no income statement / balance sheet for tokens.",
            ],
            risks=[
                "Crypto-specific risks (custody, regulatory, exchange counterparty, "
                "liquidity at sale) are evaluated in the Risks section.",
            ],
            citations=[],
            confidence=0.0,
        )

    c = client or get_client()
    sys_prompt = _load_prompt()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Ticker: {ticker}\nBrief: {brief}\n"
                "Use your tools and submit_findings when ready."
            ),
        }
    ]

    for _ in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 2000,
            "temperature": 0.3,
            "system": [
                {
                    "type": "text",
                    "text": sys_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": _all_tool_schemas(),
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_uses: list[ToolUseBlock] = [
            cast(ToolUseBlock, b)
            for b in resp.content
            if getattr(b, "type", None) == "tool_use"
        ]
        if not tool_uses:
            raise FundamentalError(
                "model emitted no tool calls; expected at least submit_findings"
            )

        # Append the assistant turn
        messages.append({"role": "assistant", "content": resp.content})

        # Build the user turn with tool_results
        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            if tu.name == "submit_findings":
                args = cast(dict[str, Any], tu.input)
                # Validate via pydantic before returning
                return FundamentalFindings(**args)
            tool = _TOOLS_BY_NAME.get(tu.name)
            if tool is None:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"error": f"unknown tool: {tu.name}"}),
                    "is_error": True,
                })
                continue
            try:
                payload = await tool.impl(**cast(dict[str, Any], tu.input))
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": payload.model_dump_json(),
                })
            except Exception as e:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results_content})

    raise FundamentalError(
        f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit_findings"
    )
