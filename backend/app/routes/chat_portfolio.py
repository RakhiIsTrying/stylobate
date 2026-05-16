from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.portfolio_strategist import run_portfolio_strategist
from app.core.auth import get_current_user

router = APIRouter(tags=["chat-portfolio"])


class ChatPortfolioRequest(BaseModel):
    portfolio_id: str | None = None
    message: str | None = None
    target_alloc: dict[str, float] | None = None


def _format_currency(value: float, currency: str) -> str:
    sym = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(
        currency, f"{currency} "
    )
    return f"{sym}{value:,.0f}"


def _cohort_label(cohort: list[str]) -> str:
    currency, group = cohort[0], cohort[1]
    return f"{currency} ({'Crypto' if group == 'crypto' else 'Equities & ETFs'})"


def _render_snapshot_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])
        currency = c["cohort"][0]
        value = _format_currency(c["total_value_native"], currency)
        gain = f"{c['gain_pct']:+.1f}%" if c.get("gain_pct") is not None else "—"
        lines.append(f"- **{label}**: {value} ({gain}, {c['positions_count']} positions)")
        weights_top = sorted(c.get("weights", {}).items(), key=lambda x: -x[1])[:5]
        if weights_top:
            wts = ", ".join(f"{t} {w * 100:.1f}%" for t, w in weights_top)
            lines.append(f"  - weights: {wts}")
    return "\n".join(lines) or "_No positions._"


def _render_returns_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])

        def fmt(v: float | None) -> str:
            return f"{v:+.1f}%" if v is not None else "—"

        lines.append(
            f"- **{label}** — 1mo {fmt(c.get('returns_1mo'))}, "
            f"3mo {fmt(c.get('returns_3mo'))}, "
            f"1y {fmt(c.get('returns_1y'))} "
            f"vs benchmark {c.get('benchmark_ticker')}: "
            f"1y {fmt(c.get('benchmark_returns_1y'))}"
        )
    return "\n".join(lines) or "_Returns unavailable._"


def _render_risk_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])

        def fmt(v: float | None, digits: int = 2) -> str:
            return f"{v:.{digits}f}" if v is not None else "—"

        dd = c.get("max_drawdown_1y")
        dd_pct = f"{dd * 100:+.1f}%" if dd is not None else "—"
        lines.append(
            f"- **{label}** — sharpe {fmt(c.get('sharpe_1y'))}, "
            f"beta {fmt(c.get('beta_1y'))}, max drawdown {dd_pct}"
        )
    return "\n".join(lines) or "_Risk metrics unavailable._"


def _render_rebalance_section(rebalance: dict[str, Any]) -> str:
    if not rebalance or not rebalance.get("sum_check_ok"):
        message = rebalance.get("message", "No rebalance computed.") if rebalance else ""
        return str(message)
    lines = [
        f"_Comparison currency: {rebalance.get('comparison_currency', 'native_only')}_",
    ]
    rates = rebalance.get("assumed_fx_rates") or {}
    if rates:
        lines.append(f"_Assumed FX: USDINR = {rates.get('USDINR'):.2f}_")
    for t in rebalance.get("cohort_trades", []):
        cohort = t["cohort"]
        currency = cohort[0]
        delta = t["delta_native"]
        action = t["action"]
        if action == "hold":
            lines.append(f"- **{_cohort_label(cohort)}**: hold (no change needed)")
        else:
            verb = "Add" if action == "increase" else "Reduce"
            amount = _format_currency(abs(delta), currency)
            lines.append(f"- **{_cohort_label(cohort)}**: {verb} {amount}")
    return "\n".join(lines)


async def _stream_findings(findings: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield SSE events: progress, delta(section)*, done."""
    yield (
        b"event: progress\ndata: "
        + json.dumps({"step": "analyzing_portfolio"}).encode()
        + b"\n\n"
    )

    cohorts = findings.get("cohorts", [])
    if not cohorts:
        yield (
            b"event: delta\ndata: "
            + json.dumps(
                {
                    "type": "section",
                    "title": "Portfolio",
                    "markdown": findings.get("notes", ["No portfolio data."])[0],
                    "citations": [],
                }
            ).encode()
            + b"\n\n"
        )
    else:
        yield (
            b"event: delta\ndata: "
            + json.dumps(
                {
                    "type": "section",
                    "title": "Portfolio Snapshot",
                    "markdown": _render_snapshot_section(cohorts),
                    "citations": [],
                }
            ).encode()
            + b"\n\n"
        )
        yield (
            b"event: delta\ndata: "
            + json.dumps(
                {
                    "type": "section",
                    "title": "Returns vs Benchmark",
                    "markdown": _render_returns_section(cohorts),
                    "citations": [],
                }
            ).encode()
            + b"\n\n"
        )
        yield (
            b"event: delta\ndata: "
            + json.dumps(
                {
                    "type": "section",
                    "title": "Risk Metrics",
                    "markdown": _render_risk_section(cohorts),
                    "citations": [],
                }
            ).encode()
            + b"\n\n"
        )
        if findings.get("rebalance"):
            yield (
                b"event: delta\ndata: "
                + json.dumps(
                    {
                        "type": "section",
                        "title": "Rebalance Plan",
                        "markdown": _render_rebalance_section(findings["rebalance"]),
                        "citations": [],
                    }
                ).encode()
                + b"\n\n"
            )
    if findings.get("notes"):
        yield (
            b"event: delta\ndata: "
            + json.dumps(
                {
                    "type": "section",
                    "title": "Notes",
                    "markdown": "\n".join(f"- {n}" for n in findings["notes"]),
                    "citations": [],
                }
            ).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"


@router.post("/chat/portfolio")
async def chat_portfolio(
    req: ChatPortfolioRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    findings = await run_portfolio_strategist(
        user_id=user["sub"],
        portfolio_id=req.portfolio_id,
        brief=req.message or "Snapshot of my current portfolio.",
        target_alloc=req.target_alloc,
    )
    return StreamingResponse(_stream_findings(findings), media_type="text/event-stream")
