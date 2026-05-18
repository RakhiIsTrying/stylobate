from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.data.universes import list_universe_names, load_universe
from app.data.yfinance_adapter import (
    fetch_key_ratios,
    fetch_ticker_info,
)
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Maximum number of yfinance round-trips inside a single screen_stocks call.
# Keeps wall-clock bounded even if the LLM passes a huge shortlist.
_MAX_FETCH = 40


def build_screener_tools(*, user_id: str | None = None) -> list[Tool]:
    """Build the 4 Screener tools. user_id is captured but unused in v1
    (kept for parity with Phase 3B/3C tools that read user data)."""
    # Agent-local universe cache. Populated by resolve_universe; consumed by
    # theme_to_universe and screen_stocks. Keys: ticker → Constituent dict.
    universe_index: dict[str, dict[str, Any]] = {}
    loaded_universe_names: set[str] = set()

    async def _resolve_universe(
        universes: list[str] | None = None, **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universes:
            return {"error": "universes is required (array of names)"}
        valid_names = set(list_universe_names())
        unknown = [u for u in universes if u not in valid_names]
        if unknown:
            return {
                "error": f"unknown universe(s): {unknown}",
                "valid": sorted(valid_names),
            }
        all_constituents: list[dict[str, Any]] = []
        for name in universes:
            rows = await load_universe(name)
            for r in rows:
                universe_index[r["ticker"]] = r
            all_constituents.extend(rows)
            loaded_universe_names.add(name)
        return {
            "universes": list(universes),
            "constituents": all_constituents,
            "constituent_count": len(all_constituents),
        }

    async def _theme_to_universe(
        theme: str = "", tickers: list[str] | None = None, **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universe_index:
            return {
                "error": "no universe loaded — call resolve_universe first",
            }
        if not tickers:
            return {"theme": theme, "tickers": [], "dropped": [], "notes": []}
        kept: list[str] = []
        dropped: list[str] = []
        for t in tickers:
            if t in universe_index:
                kept.append(t)
            else:
                dropped.append(t)
        notes: list[str] = []
        if dropped:
            notes.append(
                f"Dropped {len(dropped)} ticker(s) not present in the loaded "
                f"universe(s): {dropped}"
            )
        return {"theme": theme, "tickers": kept, "dropped": dropped, "notes": notes}

    async def _screen_stocks(
        tickers: list[str] | None = None,
        min_market_cap: float | None = None,
        max_pe: float | None = None,
        min_roe: float | None = None,
        sectors: list[str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universe_index:
            return {"error": "no universe loaded — call resolve_universe first"}
        if not tickers:
            return {"candidates": [], "dropped": [], "notes": ["empty ticker list"]}

        # Pre-filter by sector using the cached constituent metadata
        survivors: list[dict[str, Any]] = []
        dropped: list[dict[str, str]] = []
        for t in tickers:
            row = universe_index.get(t)
            if row is None:
                dropped.append({"ticker": t, "reason": "not in loaded universe"})
                continue
            if sectors:
                row_sector = row.get("sector") or ""
                if row_sector not in sectors:
                    dropped.append(
                        {"ticker": t, "reason": f"sector {row_sector!r} not in allowlist"}
                    )
                    continue
            survivors.append(row)

        # Cap fetch count
        if len(survivors) > _MAX_FETCH:
            dropped.extend(
                {"ticker": r["ticker"], "reason": "trimmed (over fetch cap)"}
                for r in survivors[_MAX_FETCH:]
            )
            survivors = survivors[:_MAX_FETCH]

        # Live fetch fundamentals in parallel
        async def _enrich(row: dict[str, Any]) -> dict[str, Any]:
            ticker = row["ticker"]
            market = row["market"]
            info_res = await asyncio.gather(
                fetch_ticker_info(ticker, market=market),
                return_exceptions=True,
            )
            ratios_res = await asyncio.gather(
                fetch_key_ratios(ticker, market=market),
                return_exceptions=True,
            )
            info = info_res[0]
            ratios = ratios_res[0]
            current_price: float | None = None
            market_cap: float | None = row.get("market_cap")
            if not isinstance(info, BaseException):
                current_price = info.last_price
                if market_cap is None:
                    market_cap = info.market_cap
            pe_ttm: float | None = None
            roe_pct: float | None = None
            rev_growth: float | None = None
            if not isinstance(ratios, BaseException):
                pe_ttm = ratios.pe_ttm
                roe_pct = ratios.roe
                rev_growth = ratios.revenue_growth_yoy
            return {
                "ticker": ticker,
                "name": row["name"],
                "market": market,
                "currency": row["currency"],
                "sector": row.get("sector"),
                "current_price": current_price,
                "market_cap": market_cap,
                "pe_ttm": pe_ttm,
                "roe_pct": roe_pct,
                "revenue_growth_yoy": rev_growth,
            }

        enriched = await asyncio.gather(*[_enrich(r) for r in survivors])

        # Apply numeric filters
        candidates: list[dict[str, Any]] = []
        for c in enriched:
            if min_market_cap is not None and (
                c["market_cap"] is None or c["market_cap"] < min_market_cap
            ):
                dropped.append({"ticker": c["ticker"], "reason": "below min_market_cap"})
                continue
            if max_pe is not None and (
                c["pe_ttm"] is None or c["pe_ttm"] > max_pe
            ):
                dropped.append({"ticker": c["ticker"], "reason": "above max_pe"})
                continue
            if min_roe is not None and (
                c["roe_pct"] is None or c["roe_pct"] < min_roe
            ):
                dropped.append({"ticker": c["ticker"], "reason": "below min_roe"})
                continue
            candidates.append(c)

        notes: list[str] = []
        if dropped:
            notes.append(f"Dropped {len(dropped)} candidate(s) due to filters or missing data.")
        return {
            "candidates": candidates,
            "dropped": dropped,
            "notes": notes,
            "filters_applied": {
                "min_market_cap": min_market_cap,
                "max_pe": max_pe,
                "min_roe": min_roe,
                "sectors": sectors,
            },
        }

    return [
        Tool(
            name="resolve_universe",
            description=(
                "Load constituent list(s) for one or more universes. "
                "Universes: 'sp500' (US large-caps), 'nifty500' (Indian equities), "
                "'crypto' (top 100 by market cap)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "universes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["sp500", "nifty500", "crypto"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["universes"],
            },
            impl=_resolve_universe,
        ),
        Tool(
            name="theme_to_universe",
            description=(
                "Filter a candidate ticker list by theme. The LLM picks tickers "
                "from the loaded constituent list (call resolve_universe first). "
                "Invented tickers are dropped with a note."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["theme", "tickers"],
            },
            impl=_theme_to_universe,
        ),
        Tool(
            name="screen_stocks",
            description=(
                "Apply structured filters to a ticker shortlist. Fetches live "
                "fundamentals from yfinance for each ticker. Filters: min_market_cap "
                "(USD-equivalent for crypto), max_pe (PE TTM), min_roe (percent), "
                "sectors (allowlist)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "min_market_cap": {"type": ["number", "null"]},
                    "max_pe": {"type": ["number", "null"]},
                    "min_roe": {"type": ["number", "null"]},
                    "sectors": {"type": ["array", "null"], "items": {"type": "string"}},
                },
                "required": ["tickers"],
            },
            impl=_screen_stocks,
        ),
    ]
