from __future__ import annotations

from datetime import date as date_cls
from typing import Any

from app.data.fx import get_usdinr
from app.data.portfolio_stats import CohortKey, compute_cohort_stats
from app.data.prices_cache import get_prices
from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history_for_position
from app.db import portfolios as db
from app.tools.base import Tool


async def _load_holdings(
    user_id: str, portfolio_id: str | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (resolved_portfolio_id, positions). If portfolio_id is None,
    auto-select the user's first portfolio. Returns (None, []) if no portfolios."""
    if portfolio_id is None:
        portfolios = await db.list_portfolios(user_id)
        if not portfolios:
            return None, []
        portfolio_id = str(portfolios[0]["id"])
    if not await db.assert_portfolio_owned(user_id, portfolio_id):
        return None, []
    positions = await db.list_positions(user_id, portfolio_id)
    return portfolio_id, positions


async def _holdings_with_prices(
    user_id: str, portfolio_id: str | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (portfolio_id, positions-with-current-price)."""
    pid, positions = await _load_holdings(user_id, portfolio_id)
    if not positions:
        return pid, []
    pairs = [(p["ticker"], p["market"]) for p in positions]
    prices = await get_prices(pairs)
    enriched: list[dict[str, Any]] = []
    for p in positions:
        price = prices.get((p["ticker"], p["market"]))
        enriched.append({**p, "current_price": price.price if price else None})
    return pid, enriched


def build_portfolio_tools(*, user_id: str, portfolio_id: str | None = None) -> list[Tool]:
    """Build a fresh set of 4 tools whose impls capture user_id + portfolio_id.

    The LLM-visible schemas DO NOT include user_id. Callers (the LLM) cannot
    influence which user's data is read.
    """

    async def _get_holdings(**_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        return {
            "portfolio_id": pid,
            "holdings_count": len(positions),
            "positions": [
                {
                    "ticker": p["ticker"], "market": p["market"],
                    "asset_class": p["asset_class"],
                    "quantity": float(p["quantity"]),
                    "cost_basis": float(p["cost_basis"]) if p["cost_basis"] is not None else None,
                    "currency": p["currency"],
                    "opened_at": p["opened_at"].isoformat() if p.get("opened_at") else None,
                    "current_price": p["current_price"],
                }
                for p in positions
            ],
        }

    async def _calc_portfolio_stats(**_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "cohorts": []}
        cohort_keys: set[CohortKey] = {
            (p["currency"], "crypto" if p["asset_class"] == "crypto" else "equity_etf")
            for p in positions
        }
        from app.data.benchmarks import get_benchmark_history
        benchmark_history: dict[CohortKey, list[tuple[date_cls, float]]] = {}
        for key in cohort_keys:
            benchmark_history[key] = await get_benchmark_history(key)
        price_history: dict[str, list[tuple[date_cls, float]]] = {}
        for p in positions:
            try:
                bars = await _fetch_price_history_for_position(
                    p["ticker"], period="1y", interval="1d", market=p["market"],
                )
            except Exception:
                bars = []
            price_history[p["ticker"]] = [(b.date, float(b.close)) for b in bars]
        risk_free: dict[CohortKey, float] = {
            ("USD", "equity_etf"): 4.5, ("USD", "crypto"): 4.5,
            ("INR", "equity_etf"): 6.5, ("INR", "crypto"): 4.5,
        }
        cohorts = compute_cohort_stats(positions, price_history, benchmark_history, risk_free)
        return {"portfolio_id": pid, "cohorts": cohorts}

    async def _suggest_rebalance(
        target_alloc: dict[str, float] | None = None,
        mode: str = "cross_cohort_fx",
        suggest_per_position: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not target_alloc:
            return {"sum_check_ok": False, "message": "target_alloc is required"}
        s = sum(target_alloc.values())
        sum_check_ok = abs(s - 1.0) < 0.001
        if not sum_check_ok:
            return {"sum_check_ok": False,
                    "message": f"target_alloc sums to {s:.3f}, must sum to 1.0"}

        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"sum_check_ok": True, "portfolio_id": pid, "cohort_trades": []}

        # Bucket by cohort
        from collections import defaultdict
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for p in positions:
            group = "crypto" if p["asset_class"] == "crypto" else "equity_etf"
            buckets[(p["currency"], group)].append(p)

        # Cohort values (native)
        cohort_value_native: dict[tuple[str, str], float] = {}
        for key, ps in buckets.items():
            cohort_value_native[key] = sum(
                float(p["quantity"]) * float(p["current_price"] or 0)
                for p in ps
            )

        # Optional FX comparison
        usdinr: float | None = None
        comparison_currency = "native_only"
        if mode == "cross_cohort_fx":
            usdinr = await get_usdinr()
            if usdinr is not None:
                comparison_currency = "USD"

        # Convert each cohort to USD for the gap math
        cohort_value_usd: dict[tuple[str, str], float] = {}
        for key, value in cohort_value_native.items():
            if comparison_currency == "USD":
                cohort_value_usd[key] = value / usdinr if key[0] == "INR" else value  # type: ignore[operator]
            else:
                cohort_value_usd[key] = value  # treat as native, no cross-currency

        total_usd = sum(cohort_value_usd.values()) or 1.0
        current_alloc_pct = {
            f"{k[0]}:{k[1]}": cohort_value_usd[k] / total_usd
            for k in cohort_value_usd
        }

        trades: list[dict[str, Any]] = []
        for target_key, target_pct in target_alloc.items():
            currency, group = target_key.split(":", 1)
            key = (currency, group)
            current_usd = cohort_value_usd.get(key, 0.0)
            target_usd = total_usd * target_pct
            delta_usd = target_usd - current_usd
            if comparison_currency == "USD" and currency == "INR" and usdinr:
                delta_native = delta_usd * usdinr
            else:
                delta_native = delta_usd
            tol = 0.01 * (cohort_value_native.get(key, 1.0) or 1.0)
            if abs(delta_native) < tol:
                action = "hold"
            else:
                action = "increase" if delta_native > 0 else "decrease"

            hints: list[dict[str, Any]] = []
            if suggest_per_position and key in buckets and abs(delta_native) > 0:
                ps_in_cohort = buckets[key]
                cohort_value = cohort_value_native.get(key, 0.0)
                if cohort_value > 0:
                    for p in ps_in_cohort:
                        pos_value = float(p["quantity"]) * float(p["current_price"] or 0)
                        weight = pos_value / cohort_value
                        pos_delta = delta_native * weight
                        if abs(pos_delta) < 0.01:
                            continue
                        # Cap at 50% of current position
                        cap = pos_value * 0.5
                        capped = max(-cap, min(cap, pos_delta))
                        price_for_qty = float(p["current_price"]) if p["current_price"] else 0.0
                        qty_change = capped / price_for_qty if price_for_qty else 0.0
                        hints.append({
                            "ticker": p["ticker"],
                            "action": "buy" if capped > 0 else "sell",
                            "qty": abs(qty_change),
                            "est_value_native": abs(capped),
                        })

            trades.append({
                "cohort": list(key),
                "action": action,
                "delta_native": delta_native,
                "per_position_hints": hints,
            })

        return {
            "sum_check_ok": True,
            "portfolio_id": pid,
            "target_alloc": target_alloc,
            "comparison_currency": comparison_currency,
            "assumed_fx_rates": {"USDINR": usdinr} if usdinr is not None else {},
            "current_alloc_pct": current_alloc_pct,
            "cohort_trades": trades,
        }

    async def _tax_lot_view(ticker: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "lots": []}
        if ticker:
            positions = [p for p in positions if p["ticker"].upper() == ticker.upper()]
        lots: list[dict[str, Any]] = []
        for p in positions:
            qty = float(p["quantity"])
            basis = float(p["cost_basis"] or 0)
            price = float(p["current_price"] or 0)
            lots.append({
                "ticker": p["ticker"],
                "qty": qty,
                "basis": basis,
                "current_value": qty * price if price else 0.0,
                "gain": (price - basis) * qty if price else None,
                "currency": p["currency"],
                "acquired_at": p["opened_at"].isoformat() if p.get("opened_at") else None,
                "virtual": True,
            })
        return {"portfolio_id": pid, "lots": lots}

    return [
        Tool(
            name="get_holdings",
            description=(
                "Return the user's current holdings for the active portfolio. "
                "Each position includes ticker, market, asset_class, quantity, "
                "cost_basis, currency, opened_at, and live current_price."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_get_holdings,
        ),
        Tool(
            name="calc_portfolio_stats",
            description=(
                "Compute per-cohort statistics: weights, returns (1mo/3mo/1y), "
                "benchmark comparison, sharpe, beta, max drawdown. Cohorts are "
                "(currency, asset_class_group). USD-equity and USD-crypto are SEPARATE."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_calc_portfolio_stats,
        ),
        Tool(
            name="suggest_rebalance",
            description=(
                "Given a target_alloc dict (cohort_key -> fraction, must sum to 1.0), "
                "compute the gap and suggest trades in each cohort's native currency. "
                "mode='cross_cohort_fx' uses USDINR for comparison; 'per_cohort_only' "
                "treats each cohort independently."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "target_alloc": {
                        "type": "object",
                        "description": "cohort_key (e.g. 'USD:equity_etf') -> fraction",
                        "additionalProperties": {"type": "number"},
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["cross_cohort_fx", "per_cohort_only"],
                        "default": "cross_cohort_fx",
                    },
                    "suggest_per_position": {"type": "boolean", "default": True},
                },
                "required": ["target_alloc"],
            },
            impl=_suggest_rebalance,
        ),
        Tool(
            name="tax_lot_view",
            description=(
                "Return tax-lot view per position. In Phase 3B the tax_lots table is "
                "unused so each position appears as one virtual lot (qty/basis/opened_at). "
                "Optional ticker filter."
            ),
            input_schema={
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": [],
            },
            impl=_tax_lot_view,
        ),
    ]
