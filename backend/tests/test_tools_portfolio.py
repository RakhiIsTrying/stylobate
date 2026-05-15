from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.mark.asyncio
async def test_get_holdings_uses_closure_user_id_not_args() -> None:
    """The LLM cannot pass user_id; it's captured at build time."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value=[{"id": UUID(PID)}])

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    get_holdings = next(t for t in tools if t.name == "get_holdings")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})):
        # LLM tries to pass user_id — should be ignored
        result = await get_holdings.impl(user_id="ATTACKER_UUID")

    # The actual SQL must use the closure user_id, not "ATTACKER_UUID"
    actual_args = conn.fetch.await_args.args
    assert UUID(USER_ID) in actual_args
    assert "ATTACKER_UUID" not in [str(a) for a in actual_args]
    assert result["holdings_count"] == 1


@pytest.mark.asyncio
async def test_get_holdings_returns_empty_when_user_has_no_portfolios() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)

    from app.tools.portfolio import build_portfolio_tools
    # No portfolio_id passed -> tool picks first portfolio; if none, returns empty
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=None)
    get_holdings = next(t for t in tools if t.name == "get_holdings")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})):
        result = await get_holdings.impl()

    assert result["holdings_count"] == 0
    assert result["portfolio_id"] is None


@pytest.mark.asyncio
async def test_calc_portfolio_stats_aggregates_per_cohort() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "BTC", "market": "CRYPTO",
         "asset_class": "crypto", "quantity": 0.5, "cost_basis": 40000.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from datetime import datetime

    from app.data.prices_cache import Price
    now = datetime(2026, 5, 15, 0, 0, 0)
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=120.0,
                              currency="USD", as_of=now),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80000.0,
                                 currency="USD", as_of=now),
    }

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    calc = next(t for t in tools if t.name == "calc_portfolio_stats")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.data.benchmarks.get_benchmark_history",
               AsyncMock(return_value=[])), \
         patch("app.tools.portfolio._fetch_price_history_for_position",
               AsyncMock(return_value=[])):
        result = await calc.impl()

    cohorts = result["cohorts"]
    assert len(cohorts) == 2
    keys = {tuple(c["cohort"]) for c in cohorts}
    assert ("USD", "equity_etf") in keys
    assert ("USD", "crypto") in keys


@pytest.mark.asyncio
async def test_suggest_rebalance_target_must_sum_to_one() -> None:
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    rebalance = next(t for t in tools if t.name == "suggest_rebalance")

    # invalid: sums to 0.9, not 1.0
    bad = {"USD:equity_etf": 0.5, "INR:equity_etf": 0.4}
    result = await rebalance.impl(target_alloc=bad)
    assert result["sum_check_ok"] is False


@pytest.mark.asyncio
async def test_suggest_rebalance_computes_deltas() -> None:
    """Single USD equity position; user wants 70/30 USD/INR. Should suggest reducing USD."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from datetime import datetime

    from app.data.prices_cache import Price
    now = datetime(2026, 5, 15)
    fake_prices = {("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                                          currency="USD", as_of=now)}

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    rebalance = next(t for t in tools if t.name == "suggest_rebalance")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.portfolio.get_usdinr", AsyncMock(return_value=83.0)):
        target = {"USD:equity_etf": 0.7, "INR:equity_etf": 0.3}
        result = await rebalance.impl(target_alloc=target)

    assert result["sum_check_ok"] is True
    # AAPL value is 10*200=2000 USD. Target = 70% in USD = 1400 USD,
    # 30% in INR = 600 USD eq = ~49800 INR. We need to REDUCE USD by 600 and
    # ADD INR by 600 USD equivalent (49800 INR).
    by_cohort = {tuple(t["cohort"]): t for t in result["cohort_trades"]}
    usd = by_cohort[("USD", "equity_etf")]
    assert usd["action"] == "decrease"
    assert abs(usd["delta_native"] - (-600.0)) < 1.0


@pytest.mark.asyncio
async def test_tax_lot_view_returns_virtual_lot_from_position() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from datetime import datetime

    from app.data.prices_cache import Price
    fake_prices = {("AAPL", "US"): Price(ticker="AAPL", market="US", price=150.0,
                                          currency="USD", as_of=datetime(2026, 5, 15))}

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    tax_lot = next(t for t in tools if t.name == "tax_lot_view")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)):
        result = await tax_lot.impl()

    assert len(result["lots"]) == 1
    lot = result["lots"][0]
    assert lot["ticker"] == "AAPL"
    assert lot["qty"] == 10
    assert lot["basis"] == 100.0
    assert lot["current_value"] == 1500.0  # 10 * 150
    assert abs(lot["gain"] - 500.0) < 1e-6  # (150-100)*10
