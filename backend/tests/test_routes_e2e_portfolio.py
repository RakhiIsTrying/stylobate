from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.data.prices_cache import Price

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"
PORTFOLIO_ID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any:
            return value
        async def __aexit__(self, *_a: Any) -> None:
            pass
    return _CM()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_positions_groups_by_cohort(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PORTFOLIO_ID)})
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-0000-0000-0000000000a1"),
                "portfolio_id": UUID(PORTFOLIO_ID),
                "ticker": "AAPL",
                "market": "US",
                "asset_class": "equity",
                "quantity": 50,
                "cost_basis": 175.0,
                "currency": "USD",
                "opened_at": date(2024, 3, 15),
                "created_at": datetime(2026, 5, 15),
            },
            {
                "id": UUID("00000000-0000-0000-0000-0000000000a2"),
                "portfolio_id": UUID(PORTFOLIO_ID),
                "ticker": "RELIANCE.NS",
                "market": "IN",
                "asset_class": "equity",
                "quantity": 100,
                "cost_basis": 1250.0,
                "currency": "INR",
                "opened_at": date(2024, 8, 2),
                "created_at": datetime(2026, 5, 15),
            },
            {
                "id": UUID("00000000-0000-0000-0000-0000000000a3"),
                "portfolio_id": UUID(PORTFOLIO_ID),
                "ticker": "BTC",
                "market": "CRYPTO",
                "asset_class": "crypto",
                "quantity": 0.5,
                "cost_basis": 42000.0,
                "currency": "USD",
                "opened_at": date(2023, 11, 10),
                "created_at": datetime(2026, 5, 15),
            },
        ]
    )
    now = datetime(2026, 5, 15, 14, 23, 0)
    fake_prices = {
        ("AAPL", "US"): Price(
            ticker="AAPL", market="US", price=189.42, currency="USD", as_of=now
        ),
        ("RELIANCE.NS", "IN"): Price(
            ticker="RELIANCE.NS", market="IN", price=1384.0, currency="INR", as_of=now
        ),
        ("BTC", "CRYPTO"): Price(
            ticker="BTC", market="CRYPTO", price=80652.0, currency="USD", as_of=now
        ),
    }
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.routes.portfolios.get_prices", AsyncMock(return_value=fake_prices)):
        r = await client.get(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 3
    # USD-equity and USD-crypto are SEPARATE cohorts
    usd_cohorts = [c for c in body["cohorts"] if c["currency"] == "USD"]
    assert len(usd_cohorts) == 2
    eq = next(c for c in usd_cohorts if c["asset_class_group"] == "equity_etf")
    cy = next(c for c in usd_cohorts if c["asset_class_group"] == "crypto")
    assert eq["positions_count"] == 1
    assert cy["positions_count"] == 1
    inr = next(c for c in body["cohorts"] if c["currency"] == "INR")
    assert inr["positions_count"] == 1
    # AAPL P/L: (189.42 - 175) / 175 * 100 ≈ 8.24
    aapl = next(p for p in body["positions"] if p["ticker"] == "AAPL")
    assert aapl["current_price"] == 189.42
    assert abs(aapl["pl_pct"] - 8.24) < 0.1


@pytest.mark.asyncio
async def test_get_positions_handles_missing_price(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """yfinance failure: position is still returned, prices_partial=true."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PORTFOLIO_ID)})
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": UUID("00000000-0000-0000-0000-0000000000a1"),
                "portfolio_id": UUID(PORTFOLIO_ID),
                "ticker": "AAPL",
                "market": "US",
                "asset_class": "equity",
                "quantity": 50,
                "cost_basis": 175.0,
                "currency": "USD",
                "opened_at": date(2024, 3, 15),
                "created_at": datetime(2026, 5, 15),
            },
        ]
    )
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.routes.portfolios.get_prices", AsyncMock(return_value={})):
        r = await client.get(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prices_partial"] is True
    assert body["positions"][0]["current_price"] is None
