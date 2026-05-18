from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import KeyRatios, TickerInfo


@pytest.mark.asyncio
async def test_resolve_universe_loads_sp500() -> None:
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "MSFT", "name": "Microsoft", "sector": "Information Technology",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]
    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        result = await resolve.impl(universes=["sp500"])
    assert result["universes"] == ["sp500"]
    assert len(result["constituents"]) == 2
    assert result["constituents"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_resolve_universe_rejects_unknown_name() -> None:
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    resolve = next(t for t in tools if t.name == "resolve_universe")
    result = await resolve.impl(universes=["nasdaq100"])
    assert "error" in result
    assert "nasdaq100" in result["error"]


@pytest.mark.asyncio
async def test_resolve_universe_combines_multiple() -> None:
    fake_sp500 = [{"ticker": "AAPL", "name": "Apple", "sector": "IT",
                   "currency": "USD", "asset_class": "equity", "market": "US"}]
    fake_nifty = [{"ticker": "TCS.NS", "name": "Tata Consultancy", "sector": "IT",
                   "currency": "INR", "asset_class": "equity", "market": "IN"}]

    async def fake_load(name: str) -> list[dict[str, Any]]:
        return {"sp500": fake_sp500, "nifty500": fake_nifty}[name]

    with patch("app.tools.screener.load_universe", side_effect=fake_load):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        result = await resolve.impl(universes=["sp500", "nifty500"])
    assert set(result["universes"]) == {"sp500", "nifty500"}
    tickers = {c["ticker"] for c in result["constituents"]}
    assert tickers == {"AAPL", "TCS.NS"}


@pytest.mark.asyncio
async def test_theme_to_universe_validates_against_loaded_universes() -> None:
    """LLM-supplied tickers that aren't in the loaded universes are dropped."""
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "NVDA", "name": "NVIDIA", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]
    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        theme = next(t for t in tools if t.name == "theme_to_universe")
        # Load the universe (populates the agent's internal cache)
        await resolve.impl(universes=["sp500"])
        # LLM picks AAPL (real), MADEUP (hallucinated), NVDA (real)
        result = await theme.impl(
            theme="AI semiconductor plays",
            tickers=["AAPL", "MADEUP", "NVDA"],
        )
    assert set(result["tickers"]) == {"AAPL", "NVDA"}
    assert "MADEUP" in result["dropped"]


@pytest.mark.asyncio
async def test_theme_to_universe_no_universe_loaded_returns_error() -> None:
    """If resolve_universe wasn't called first, theme tool returns an error."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    theme = next(t for t in tools if t.name == "theme_to_universe")
    result = await theme.impl(theme="X", tickers=["AAPL"])
    assert "error" in result


@pytest.mark.asyncio
async def test_screen_stocks_applies_filters() -> None:
    """Filters drop tickers; survivors include yfinance fundamentals."""
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "MSFT", "name": "Microsoft", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "WMT", "name": "Walmart", "sector": "Consumer Staples",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]

    async def fake_info(ticker: str, market: str = "US") -> TickerInfo:
        prices = {"AAPL": 200.0, "MSFT": 450.0, "WMT": 80.0}
        return TickerInfo(
            ticker=ticker, name=ticker, currency="USD",
            market_cap=2_000_000_000_000, last_price=prices.get(ticker, 100.0),
        )

    async def fake_ratios(ticker: str, market: str = "US") -> KeyRatios:
        # AAPL high PE, MSFT high PE + high ROE, WMT cheap
        d = {
            "AAPL": KeyRatios(ticker="AAPL", as_of=date(2026, 5, 18),
                              pe_ttm=35.0, pb=50.0, ps_ttm=8.0,
                              roe=120.0, roic=40.0, fcf_yield=None,
                              debt_to_equity=1.5, current_ratio=1.0,
                              net_margin=25.0, revenue_growth_yoy=10.0),
            "MSFT": KeyRatios(ticker="MSFT", as_of=date(2026, 5, 18),
                              pe_ttm=32.0, pb=12.0, ps_ttm=12.0,
                              roe=38.0, roic=22.0, fcf_yield=None,
                              debt_to_equity=0.4, current_ratio=1.7,
                              net_margin=36.0, revenue_growth_yoy=12.0),
            "WMT": KeyRatios(ticker="WMT", as_of=date(2026, 5, 18),
                             pe_ttm=14.0, pb=5.0, ps_ttm=0.7,
                             roe=18.0, roic=10.0, fcf_yield=None,
                             debt_to_equity=1.7, current_ratio=0.8,
                             net_margin=3.0, revenue_growth_yoy=5.0),
        }
        return d[ticker]

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)), \
         patch("app.tools.screener.fetch_ticker_info", fake_info), \
         patch("app.tools.screener.fetch_key_ratios", fake_ratios):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        screen = next(t for t in tools if t.name == "screen_stocks")
        await resolve.impl(universes=["sp500"])
        # Filter: PE < 30 → WMT only
        result = await screen.impl(
            tickers=["AAPL", "MSFT", "WMT"], max_pe=30.0,
        )
    cand_tickers = [c["ticker"] for c in result["candidates"]]
    assert cand_tickers == ["WMT"]
    assert result["candidates"][0]["pe_ttm"] == 14.0


@pytest.mark.asyncio
async def test_screen_stocks_keeps_candidates_with_missing_ratios() -> None:
    """yfinance failures don't kill the screener — candidate appears with None values."""
    fake_sp500 = [{"ticker": "AAPL", "name": "Apple", "sector": "IT",
                   "currency": "USD", "asset_class": "equity", "market": "US"}]

    async def fake_info(ticker: str, market: str = "US") -> TickerInfo:
        return TickerInfo(ticker=ticker, name="Apple", currency="USD",
                          market_cap=None, last_price=200.0)

    async def fake_ratios(ticker: str, market: str = "US") -> KeyRatios:
        raise RuntimeError("yfinance timeout")

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)), \
         patch("app.tools.screener.fetch_ticker_info", fake_info), \
         patch("app.tools.screener.fetch_key_ratios", fake_ratios):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        screen = next(t for t in tools if t.name == "screen_stocks")
        await resolve.impl(universes=["sp500"])
        # No filters → AAPL passes through
        result = await screen.impl(tickers=["AAPL"])
    assert len(result["candidates"]) == 1
    c = result["candidates"][0]
    assert c["ticker"] == "AAPL"
    assert c["current_price"] == 200.0
    assert c["pe_ttm"] is None
    assert c["roe_pct"] is None
