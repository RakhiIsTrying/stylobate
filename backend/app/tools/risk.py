from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls
from typing import Any

from app.data.fx import get_usdinr
from app.data.risk_stats import (
    concentration_check,
    historical_var,
    pairwise_correlations,
    stress_test,
)
from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history
from app.tools.base import Tool

logger = logging.getLogger(__name__)


async def _shared_load(
    user_id: str, portfolio_id: str | None, *, need_fx: bool = False,
) -> tuple[str | None, list[dict[str, Any]], float | None]:
    """Return (portfolio_id, positions-with-current-price, usdinr).

    usdinr is only fetched when need_fx=True (otherwise None) to avoid touching
    the FX cache from tools that don't need a cross-currency conversion.
    """
    from app.tools.portfolio import _holdings_with_prices
    pid, positions = await _holdings_with_prices(user_id, portfolio_id)
    usdinr = await get_usdinr() if (need_fx and positions) else None
    return pid, positions, usdinr


async def _fetch_history_for_positions(
    positions: list[dict[str, Any]],
) -> dict[str, list[tuple[date_cls, float]]]:
    """Fetch 1y daily history for each ticker. Failures map to empty lists."""
    out: dict[str, list[tuple[date_cls, float]]] = {}
    for p in positions:
        try:
            bars = await _fetch_price_history(
                p["ticker"], period="1y", interval="1d", market=p["market"],
            )
        except Exception as e:
            logger.warning("history fetch failed for %s: %s", p["ticker"], e)
            bars = []
        out[p["ticker"]] = [(b.date, float(b.close)) for b in bars]
    return out


def _cohort_key(p: dict[str, Any]) -> tuple[str, str]:
    group = "crypto" if p["asset_class"] == "crypto" else "equity_etf"
    return (p["currency"], group)


def build_risk_tools(*, user_id: str, portfolio_id: str | None = None) -> list[Tool]:
    """Build 4 risk tools whose impls capture user_id + portfolio_id."""

    async def _concentration_check(**_kwargs: Any) -> dict[str, Any]:
        pid, positions, usdinr = await _shared_load(user_id, portfolio_id, need_fx=True)
        flags = concentration_check(positions, usdinr)
        notes: list[str] = []
        if positions and usdinr is None and any(p["currency"] == "INR" for p in positions):
            notes.append(
                "USDINR unavailable — concentration computed without cross-currency comparison."
            )
        return {
            "portfolio_id": pid,
            "flags": flags,
            "fx_used": usdinr,
            "notes": notes,
        }

    async def _calc_var(
        confidence: float = 0.95, horizon_days: int = 10, **_kwargs: Any,
    ) -> dict[str, Any]:
        pid, positions, _ = await _shared_load(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "var_by_cohort": []}
        price_history = await _fetch_history_for_positions(positions)
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for p in positions:
            buckets[_cohort_key(p)].append(p)
        out: list[dict[str, Any]] = []
        for cohort, ps in buckets.items():
            result = historical_var(
                price_history, ps,
                confidence=confidence, horizon_days=horizon_days,
            )
            out.append({
                "cohort": [cohort[0], cohort[1]],
                "confidence": confidence,
                "horizon_days": horizon_days,
                "methodology": "historical",
                **result,
            })
        return {"portfolio_id": pid, "var_by_cohort": out}

    async def _get_correlations(**_kwargs: Any) -> dict[str, Any]:
        pid, positions, _ = await _shared_load(user_id, portfolio_id)
        if not positions or len(positions) < 2:
            return {"portfolio_id": pid, "tickers": [], "matrix": [], "excluded": []}
        price_history = await _fetch_history_for_positions(positions)
        result = pairwise_correlations(positions, price_history)
        return {"portfolio_id": pid, **result}

    async def _stress_test(scenario: str = "", **_kwargs: Any) -> dict[str, Any]:
        valid = {"rates_+200bps", "equity_-20%", "inr_depreciation_-10%"}
        if scenario not in valid:
            return {
                "error": f"unknown scenario: {scenario!r}",
                "valid_scenarios": sorted(valid),
            }
        pid, positions, usdinr = await _shared_load(user_id, portfolio_id, need_fx=True)
        if not positions:
            return {"portfolio_id": pid, "scenario": scenario,
                    "per_position": [], "by_cohort": [], "total_delta_usd": 0.0,
                    "assumed_fx": {}}
        result = stress_test(positions, scenario=scenario, usdinr=usdinr)
        return {"portfolio_id": pid, **result}

    return [
        Tool(
            name="concentration_check",
            description=(
                "Flag positions whose USD-equivalent weight exceeds 10% of the portfolio. "
                "Severity: 'warn' (10-20%) or 'critical' (>20%). Falls back to per-cohort "
                "weights if USDINR is unavailable."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_concentration_check,
        ),
        Tool(
            name="calc_var",
            description=(
                "Compute historical Value-at-Risk per cohort. var_pct is negative; "
                "var_native is the absolute amount in cohort's currency. Requires >=100 days "
                "of price history per position; insufficient cohorts return null."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "confidence": {"type": "number", "default": 0.95},
                    "horizon_days": {"type": "integer", "default": 10},
                },
                "required": [],
            },
            impl=_calc_var,
        ),
        Tool(
            name="get_correlations",
            description=(
                "Pairwise Pearson correlation matrix on daily log-returns over 1y. "
                "Positions with <100 days of overlap are excluded."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_get_correlations,
        ),
        Tool(
            name="stress_test",
            description=(
                "Apply a pre-canned stress scenario. Returns per-position deltas in native "
                "currency, by-cohort totals, and a USD-equivalent total via USDINR. "
                "Scenarios: 'rates_+200bps' (equity -2%, crypto -5%), 'equity_-20%' "
                "(equity -20%, crypto -30%), 'inr_depreciation_-10%' (INR cohort loses 10% "
                "USD-equivalent; native value unchanged)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"],
                    },
                },
                "required": ["scenario"],
            },
            impl=_stress_test,
        ),
    ]
