from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.screener import run_screener
from app.core.auth import get_current_user

router = APIRouter(tags=["chat-screen"])


class ChatScreenRequest(BaseModel):
    message: str


def _format_market_cap(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    sym = {"USD": "$", "INR": "₹"}.get(currency, "")
    if value >= 1e12:
        return f"{sym}{value / 1e12:.2f}T"
    if value >= 1e9:
        return f"{sym}{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{sym}{value / 1e6:.0f}M"
    return f"{sym}{value:,.0f}"


def _format_price(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    sym = {"USD": "$", "INR": "₹"}.get(currency, "")
    return f"{sym}{value:,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _render_theme_section(theme: str, universes_used: list[str]) -> str:
    label_map = {"sp500": "S&P 500", "nifty500": "Nifty 500", "crypto": "Crypto top-100"}
    labels = [label_map.get(u, u) for u in universes_used]
    return (
        f"**Theme:** {theme}\n"
        f"**Universe(s):** {', '.join(labels) if labels else 'n/a'}"
    )


def _render_candidates_section(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "_No candidates matched. Try a different theme or loosen filters._"
    # Detect crypto-only set (no PE/ROE columns)
    is_crypto = all(c.get("market") == "CRYPTO" for c in candidates)
    if is_crypto:
        lines = ["| Ticker | Name | Price | Market Cap |", "|---|---|---|---|"]
        for c in candidates:
            currency = c.get("currency") or "USD"
            lines.append(
                f"| {c['ticker']} | {c.get('name', '')} | "
                f"{_format_price(c.get('current_price'), currency)} | "
                f"{_format_market_cap(c.get('market_cap'), currency)} |"
            )
    else:
        lines = [
            "| Ticker | Name | Sector | Price | Market Cap | P/E | ROE |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in candidates:
            currency = c.get("currency") or "USD"
            lines.append(
                f"| {c['ticker']} | {c.get('name', '')} | "
                f"{c.get('sector') or '—'} | "
                f"{_format_price(c.get('current_price'), currency)} | "
                f"{_format_market_cap(c.get('market_cap'), currency)} | "
                f"{_format_pct(c.get('pe_ttm'))} | "
                f"{_format_pct(c.get('roe_pct'))} |"
            )
    return "\n".join(lines)


def _render_filters_section(filters: dict[str, Any]) -> str:
    if not filters or all(v is None or v == [] for v in filters.values()):
        return "_No structured filters applied._"
    bits: list[str] = []
    for k, v in filters.items():
        if v is None or v == []:
            continue
        bits.append(f"- **{k}**: {v}")
    return "\n".join(bits) or "_No structured filters applied._"


def _render_notes_section(notes: list[str]) -> str:
    if not notes:
        return "_No notes._"
    return "\n".join(f"- {n}" for n in notes)


async def _stream_findings(findings: dict[str, Any]) -> AsyncIterator[bytes]:
    yield (
        b"event: progress\ndata: "
        + json.dumps({"step": "screening"}).encode()
        + b"\n\n"
    )

    yield (
        b"event: delta\ndata: "
        + json.dumps({
            "type": "section",
            "title": "Theme",
            "markdown": _render_theme_section(
                findings.get("theme", ""),
                findings.get("universes_used", []),
            ),
            "citations": [],
        }).encode()
        + b"\n\n"
    )
    yield (
        b"event: delta\ndata: "
        + json.dumps({
            "type": "section",
            "title": "Candidates",
            "markdown": _render_candidates_section(findings.get("candidates", [])),
            "citations": findings.get("citations", []),
        }).encode()
        + b"\n\n"
    )
    if findings.get("filters_applied"):
        yield (
            b"event: delta\ndata: "
            + json.dumps({
                "type": "section",
                "title": "Filters Applied",
                "markdown": _render_filters_section(findings["filters_applied"]),
                "citations": [],
            }).encode()
            + b"\n\n"
        )
    if findings.get("notes"):
        yield (
            b"event: delta\ndata: "
            + json.dumps({
                "type": "section",
                "title": "Notes",
                "markdown": _render_notes_section(findings["notes"]),
                "citations": [],
            }).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"


@router.post("/chat/screen")
async def chat_screen(
    req: ChatScreenRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    findings = await run_screener(
        user_message=req.message,
        user_id=user["sub"],
    )
    return StreamingResponse(_stream_findings(findings), media_type="text/event-stream")
