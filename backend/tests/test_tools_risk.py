from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.data.prices_cache import Price
from app.data.yfinance_adapter import Bar

USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


def _bars(n: int) -> list[Bar]:
    return [
        Bar(date=date(2025, 5, 17), open=100.0, high=100.0, low=100.0, close=100.0 + i, volume=0)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_concentration_check_uses_closure_user_id() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 100, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "MSFT", "market": "US",
         "asset_class": "equity", "quantity": 5, "cost_basis": 400.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
        ("MSFT", "US"): Price(ticker="MSFT", market="US", price=400.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    conc = next(t for t in tools if t.name == "concentration_check")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        # LLM tries to override user_id
        result = await conc.impl(user_id="ATTACKER")

    # Closure user_id was used, not "ATTACKER"
    actual_args = conn.fetch.await_args.args
    assert UUID(USER_ID) in actual_args
    assert "ATTACKER" not in [str(a) for a in actual_args]
    # AAPL is 100*200=20000, MSFT is 5*400=2000, total 22000. AAPL = 90.9%.
    flags = result["flags"]
    assert len(flags) == 1
    assert flags[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_calc_var_aggregates_per_cohort() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "BTC", "market": "CRYPTO",
         "asset_class": "crypto", "quantity": 1, "cost_basis": 30000.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=150.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80000.0,
                                 currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    calc = next(t for t in tools if t.name == "calc_var")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk._fetch_price_history",
               AsyncMock(return_value=_bars(20))):  # insufficient history
        result = await calc.impl()

    cohorts = {tuple(c["cohort"]): c for c in result["var_by_cohort"]}
    assert ("USD", "equity_etf") in cohorts
    assert ("USD", "crypto") in cohorts
    # All insufficient_history=True because we returned only 20 bars
    assert all(c["insufficient_history"] for c in cohorts.values())


@pytest.mark.asyncio
async def test_get_correlations_returns_matrix() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "A", "market": "US",
         "asset_class": "equity", "quantity": 1, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "B", "market": "US",
         "asset_class": "equity", "quantity": 1, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("A", "US"): Price(ticker="A", market="US", price=200.0,
                           currency="USD", as_of=datetime(2026, 5, 17)),
        ("B", "US"): Price(ticker="B", market="US", price=200.0,
                           currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    corr = next(t for t in tools if t.name == "get_correlations")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk._fetch_price_history",
               AsyncMock(return_value=_bars(200))):
        result = await corr.impl()

    assert "A" in result["tickers"]
    assert "B" in result["tickers"]
    assert len(result["matrix"]) == 2
    assert result["matrix"][0][0] == 1.0  # diagonal


@pytest.mark.asyncio
async def test_stress_test_rejects_unknown_scenario() -> None:
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    stress = next(t for t in tools if t.name == "stress_test")
    result = await stress.impl(scenario="unknown_scenario")
    assert "error" in result


@pytest.mark.asyncio
async def test_stress_test_equity_minus_20_pct_on_aapl() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=100.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    stress = next(t for t in tools if t.name == "stress_test")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        result = await stress.impl(scenario="equity_-20%")

    assert result["scenario"] == "equity_-20%"
    # AAPL 10*100=1000, -20% = -200
    assert abs(result["total_delta_usd"] - (-200.0)) < 1e-6


@pytest.mark.asyncio
async def test_concentration_check_no_portfolios_returns_empty_flags() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=None)
    conc = next(t for t in tools if t.name == "concentration_check")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        result = await conc.impl()

    assert result["flags"] == []
    assert result["portfolio_id"] is None
