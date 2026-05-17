from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.portfolio_strategist import run_portfolio_strategist
from app.agents.risk_manager import run_risk_manager
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


def _render_concentration_section(flags: list[dict[str, Any]]) -> str:
    if not flags:
        return "_No concentration flags. Largest single position is under 10% of the portfolio._"
    lines: list[str] = []
    for f in flags:
        weight = f.get("weight_pct", 0) * 100
        severity = f.get("severity", "warn")
        marker = "🔴" if severity == "critical" else "🟡"
        lines.append(f"- {marker} **{f.get('ticker')}**: {weight:.1f}% — {severity}")
    return "\n".join(lines)


def _render_var_section(var_by_cohort: list[dict[str, Any]]) -> str:
    if not var_by_cohort:
        return "_VaR unavailable._"
    lines: list[str] = []
    for v in var_by_cohort:
        cohort = v.get("cohort", ["?", "?"])
        label = _cohort_label(cohort)
        conf = v.get("confidence", 0.95)
        horizon = v.get("horizon_days", 10)
        if v.get("insufficient_history") or v.get("var_pct") is None:
            lines.append(f"- **{label}** — insufficient history (need ≥100 daily closes)")
            continue
        pct = v["var_pct"] * 100
        native = v.get("var_native")
        currency = cohort[0]
        native_str = (
            _format_currency(abs(native), currency) if native is not None else "—"
        )
        lines.append(
            f"- **{label}** — {int(conf * 100)}% / {horizon}-day VaR: "
            f"{pct:+.1f}% (≈ {native_str} loss)"
        )
    return "\n".join(lines)


def _render_correlations_section(corr: dict[str, Any] | None) -> str:
    if not corr or not corr.get("tickers"):
        return "_Not enough positions with overlapping history to compute correlations._"
    tickers = corr["tickers"]
    matrix = corr["matrix"]
    excluded = corr.get("excluded", [])
    if len(tickers) < 2:
        return "_Need at least 2 positions with overlapping history._"
    # Top-5 highest absolute correlations (excluding diagonal)
    pairs: list[tuple[str, str, float]] = []
    for i, ti in enumerate(tickers):
        for j, tj in enumerate(tickers):
            if i >= j:
                continue
            pairs.append((ti, tj, matrix[i][j]))
    pairs.sort(key=lambda p: -abs(p[2]))
    lines: list[str] = ["**Top correlations:**"]
    for ti, tj, r in pairs[:5]:
        lines.append(f"- {ti} ↔ {tj}: {r:+.2f}")
    if excluded:
        lines.append(f"_Excluded (insufficient history): {', '.join(excluded)}_")
    return "\n".join(lines)


def _render_stress_section(stress_results: list[dict[str, Any]]) -> str:
    if not stress_results:
        return "_No stress test results._"
    lines: list[str] = []
    for s in stress_results:
        scenario = s.get("scenario", "?")
        total = s.get("total_delta_usd", 0)
        per_pos = s.get("per_position", [])
        worst = (
            min(per_pos, key=lambda p: p.get("delta_native", 0))
            if per_pos else None
        )
        worst_str = ""
        if worst:
            worst_str = (
                f" — worst hit: **{worst['ticker']}** "
                f"({worst.get('delta_pct', 0):+.1f}%)"
            )
        lines.append(
            f"- **{scenario}**: portfolio Δ ≈ "
            f"{_format_currency(total, 'USD')} (USD-eq){worst_str}"
        )
    return "\n".join(lines)


async def _stream_findings(combined: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield SSE events: progress, delta(section)*, done."""
    yield (
        b"event: progress\ndata: "
        + json.dumps({"step": "analyzing_portfolio"}).encode()
        + b"\n\n"
    )

    strategist = combined.get("strategist") or {}
    risk = combined.get("risk") or {}
    errors = combined.get("errors", {})

    cohorts = strategist.get("cohorts", [])
    if not cohorts and not risk.get("concentration") and not risk.get("stress_results"):
        msg = (strategist.get("notes") or risk.get("notes") or ["No portfolio data."])[0]
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Portfolio",
                          "markdown": msg, "citations": []}).encode()
            + b"\n\n"
        )
    else:
        if cohorts:
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Portfolio Snapshot",
                              "markdown": _render_snapshot_section(cohorts),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Returns vs Benchmark",
                              "markdown": _render_returns_section(cohorts),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Risk Metrics",
                              "markdown": _render_risk_section(cohorts),
                              "citations": []}).encode()
                + b"\n\n"
            )
        if risk:
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Concentration",
                              "markdown": _render_concentration_section(
                                  risk.get("concentration", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Value at Risk",
                              "markdown": _render_var_section(
                                  risk.get("var_by_cohort", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Correlations",
                              "markdown": _render_correlations_section(
                                  risk.get("correlations")),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Stress Tests",
                              "markdown": _render_stress_section(
                                  risk.get("stress_results", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
        if strategist.get("rebalance"):
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Rebalance Plan",
                              "markdown": _render_rebalance_section(strategist["rebalance"]),
                              "citations": []}).encode()
                + b"\n\n"
            )
    combined_notes: list[str] = []
    if strategist.get("notes"):
        combined_notes.extend(strategist["notes"])
    if risk.get("notes"):
        combined_notes.extend(risk["notes"])
    for source, err in errors.items():
        combined_notes.append(f"{source.capitalize()} analysis unavailable: {err}")
    if combined_notes:
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Notes",
                          "markdown": "\n".join(f"- {n}" for n in combined_notes),
                          "citations": []}).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"


@router.post("/chat/portfolio")
async def chat_portfolio(
    req: ChatPortfolioRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    user_id = user["sub"]
    brief = req.message or "Snapshot of my current portfolio."
    results = await asyncio.gather(
        run_portfolio_strategist(
            user_id=user_id, portfolio_id=req.portfolio_id,
            brief=brief, target_alloc=req.target_alloc,
        ),
        run_risk_manager(
            user_id=user_id, portfolio_id=req.portfolio_id, brief=brief,
        ),
        return_exceptions=True,
    )
    combined: dict[str, Any] = {"errors": {}}
    if isinstance(results[0], BaseException):
        combined["errors"]["strategist"] = str(results[0])
    else:
        combined["strategist"] = results[0]
    if isinstance(results[1], BaseException):
        combined["errors"]["risk"] = str(results[1])
    else:
        combined["risk"] = results[1]
    return StreamingResponse(_stream_findings(combined), media_type="text/event-stream")
