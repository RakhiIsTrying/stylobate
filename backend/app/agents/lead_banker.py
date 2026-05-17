# backend/app/agents/lead_banker.py
import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from app.agents.fundamental import run_fundamental_analysis
from app.agents.macro import run_macro_analysis
from app.agents.news_sentiment import run_news_analysis
from app.agents.portfolio_strategist import run_portfolio_strategist
from app.agents.technical import run_technical_analysis
from app.agents.ticker_resolver import TickerResolution
from app.core.anthropic_client import get_client
from app.tools.base import Tool

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
        "Specialists available: 'fundamental' (financials/valuation), 'technical' "
        "(price action/indicators), 'news' (recent headlines + sentiment), 'macro' "
        "(rates/sectors/regime). Returns combined findings JSON as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["fundamental", "technical", "news", "macro"],
                },
                "minItems": 1,
            },
            "brief": {"type": "string", "description": "One-line context for the specialists"},
        },
        "required": ["specialists", "brief"],
    },
}

dispatch_portfolio_strategist_tool = Tool(
    name="dispatch_portfolio_strategist",
    description=(
        "Run the Portfolio Strategist on the user's current portfolio. "
        "Use this ONLY when portfolio_mode is set. Pass a brief describing what "
        "they want (snapshot, rebalance, comparison). If they specified a target "
        "allocation, pass it as target_alloc dict."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "brief": {"type": "string"},
            "target_alloc": {
                "type": "object",
                "description": "cohort_key (e.g. 'USD:equity_etf') -> fraction",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["brief"],
    },
    impl=None,  # type: ignore[arg-type]  # marker tool; handled inline
)

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
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, market={resolution.market})\n\n"
        "Specialists available: fundamental, technical, news, macro.\n\n"
        "Call dispatch_specialists first with the specialists you want, then use the emit_* tools "
        "to stream the response. Emit order: quick_take → stock_card → sections → recommendation "
        "→ disclaimer → done."
    )


async def _run_dispatch(
    specialists: list[str], brief: str, ticker: str, market: str,
) -> dict[str, Any]:
    """Run the requested specialists in parallel. Returns {name: findings_dict} + errors."""
    name_to_coro: dict[str, Any] = {}
    if "fundamental" in specialists:
        name_to_coro["fundamental"] = run_fundamental_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "technical" in specialists:
        name_to_coro["technical"] = run_technical_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "news" in specialists:
        name_to_coro["news"] = run_news_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "macro" in specialists:
        name_to_coro["macro"] = run_macro_analysis(
            ticker=ticker, brief=brief, market=market,
        )

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
    btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
    if btype != "tool_use":
        return None
    name = block["name"] if isinstance(block, dict) else getattr(block, "name", "")
    args_raw = block["input"] if isinstance(block, dict) else getattr(block, "input", {})
    args = cast(dict[str, Any], args_raw) or {}
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
    resolution: TickerResolution | None,
    client: AsyncAnthropic | Any | None = None,
    portfolio_mode: bool = False,
    user_id: str | None = None,  # closure-passed to run_portfolio_strategist; never in LLM state
) -> AsyncGenerator[dict[str, Any], None]:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    if portfolio_mode:
        user_content = (
            f"User question: {user_message}\n\n"
            "Portfolio mode is active — analyze the user's portfolio. "
            "Call dispatch_portfolio_strategist exactly once with a brief, "
            "then emit_* the response. Emit order: quick_take → "
            "(skip stock_card — no single ticker) → sections → recommendation "
            "(only if rebalance was requested) → disclaimer → done.\n\n"
            "Do NOT call dispatch_specialists in portfolio mode."
        )
    else:
        assert resolution is not None, "resolution required when portfolio_mode is False"
        user_content = _build_user_message(user_message, resolution)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_content},
    ]

    tools_list: list[dict[str, Any]] = [_DISPATCH_TOOL, *_EMIT_TOOLS]
    if portfolio_mode:
        tools_list.append({
            "name": dispatch_portfolio_strategist_tool.name,
            "description": dispatch_portfolio_strategist_tool.description,
            "input_schema": dispatch_portfolio_strategist_tool.input_schema,
        })

    for _turn in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 4000,
            "system": system_blocks,
            "tools": tools_list,
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_use_blocks: list[Any] = []
        tool_results: list[dict[str, Any]] = []
        done = False

        for block in resp.content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            tool_use_blocks.append(block)
            name = block["name"] if isinstance(block, dict) else getattr(block, "name", "")
            args_raw = (
                block["input"] if isinstance(block, dict) else getattr(block, "input", {})
            )
            args = cast(dict[str, Any], args_raw) or {}
            block_id_raw = (
                block["id"] if isinstance(block, dict) else getattr(block, "id", "")
            )
            block_id = cast(str, block_id_raw)

            if name == "dispatch_specialists":
                if resolution is None:
                    err_msg = (
                        "dispatch_specialists requires a resolved ticker; "
                        "not available in portfolio_mode"
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block_id,
                        "content": json.dumps({"error": err_msg}),
                    })
                    continue
                specialists = cast(list[str], args.get("specialists", []))
                brief = cast(str, args.get("brief", ""))
                findings = await _run_dispatch(
                    specialists, brief, resolution.ticker, resolution.market,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(findings, default=str),
                })
                continue

            if name == "dispatch_portfolio_strategist":
                if not portfolio_mode or user_id is None:
                    result = {"error": "portfolio_mode not active or user_id missing"}
                else:
                    result = await run_portfolio_strategist(
                        user_id=user_id,
                        portfolio_id=None,
                        brief=cast(str, args.get("brief", "Analyze portfolio.")),
                        target_alloc=args.get("target_alloc"),
                        client=client,
                    )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(result, default=str),
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
