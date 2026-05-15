from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"
PORTFOLIO_ID = "00000000-0000-0000-0000-000000000001"
POSITION_ID = "00000000-0000-0000-0000-0000000000a1"


def _async_cm(value: Any) -> Any:
    """Wrap a value in an async context manager so it can stand in for acquire_conn."""

    class _CM:
        async def __aenter__(self) -> Any:
            return value

        async def __aexit__(self, *_a: Any) -> None:
            pass

    return _CM()


@pytest.fixture
def mock_conn() -> Any:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_add_new_position(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    """No existing position with that ticker -> INSERT path -> 201."""
    mock_conn.fetchrow.side_effect = [
        # ownership check on portfolio
        {"id": UUID(PORTFOLIO_ID)},
        # existing-position lookup (none)
        None,
        # INSERT returning row
        {
            "id": UUID(POSITION_ID),
            "portfolio_id": UUID(PORTFOLIO_ID),
            "ticker": "AAPL",
            "market": "US",
            "asset_class": "equity",
            "quantity": Decimal("50"),
            "cost_basis": Decimal("175.0"),
            "currency": "USD",
            "opened_at": date(2024, 3, 15),
            "created_at": "2026-05-15T00:00:00+00:00",
        },
    ]
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            json={
                "ticker": "AAPL",
                "market": "US",
                "asset_class": "equity",
                "quantity": 50,
                "cost_basis": 175.0,
                "currency": "USD",
                "opened_at": "2024-03-15",
            },
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 201
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["market"] == "US"
    # Decimal serialised as string by Pydantic v2
    assert Decimal(str(body["quantity"])) == Decimal("50")


@pytest.mark.asyncio
async def test_add_position_upserts_weighted_avg(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    """Existing AAPL @ 50 sh, $175. Add 50 more @ $185 -> qty 100 @ $180. HTTP 200."""
    mock_conn.fetchrow.side_effect = [
        # ownership check
        {"id": UUID(PORTFOLIO_ID)},
        # existing position row
        {
            "id": UUID(POSITION_ID),
            "portfolio_id": UUID(PORTFOLIO_ID),
            "ticker": "AAPL",
            "market": "US",
            "asset_class": "equity",
            "quantity": Decimal("50"),
            "cost_basis": Decimal("175.0"),
            "currency": "USD",
            "opened_at": date(2024, 3, 15),
            "created_at": "2026-05-15T00:00:00+00:00",
        },
        # UPDATE returning new state
        {
            "id": UUID(POSITION_ID),
            "portfolio_id": UUID(PORTFOLIO_ID),
            "ticker": "AAPL",
            "market": "US",
            "asset_class": "equity",
            "quantity": Decimal("100"),
            "cost_basis": Decimal("180.0"),
            "currency": "USD",
            "opened_at": date(2024, 3, 15),
            "created_at": "2026-05-15T00:00:00+00:00",
        },
    ]
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            json={
                "ticker": "AAPL",
                "market": "US",
                "asset_class": "equity",
                "quantity": 50,
                "cost_basis": 185.0,
                "currency": "USD",
                "opened_at": "2025-01-10",
            },
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200  # 200 for UPSERT-update path
    body = r.json()
    assert Decimal(str(body["quantity"])) == Decimal("100")
    assert Decimal(str(body["cost_basis"])) == Decimal("180.0")

    # The mock's UPDATE row is what the response echoes — that doesn't prove
    # the route did the weighted-avg math. Assert on the args passed to the
    # UPDATE fetchrow call (3rd fetchrow: ownership, existing, UPDATE).
    update_call = mock_conn.fetchrow.await_args_list[2]
    update_args = update_call.args
    # update_position dynamic SQL: $1=quantity, $2=cost_basis, $3=opened_at,
    # $4=position_id, $5=user_id
    assert Decimal(str(update_args[1])) == Decimal("100"), \
        f"route should compute new_qty=100, got {update_args[1]}"
    assert Decimal(str(update_args[2])) == Decimal("180"), \
        f"route should compute weighted-avg basis=180, got {update_args[2]}"
    # opened_at should be the earliest of the two dates (2024-03-15 < 2025-01-10)
    assert update_args[3] == date(2024, 3, 15)


@pytest.mark.asyncio
async def test_add_position_validates_market(
    client: AsyncClient,
    make_token: Callable[..., str],
) -> None:
    r = await client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={
            "ticker": "AAPL",
            "market": "EU",
            "asset_class": "equity",
            "quantity": 1,
            "cost_basis": 1.0,
            "currency": "USD",
        },
        headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_position_rejects_zero_quantity(
    client: AsyncClient,
    make_token: Callable[..., str],
) -> None:
    r = await client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={
            "ticker": "AAPL",
            "market": "US",
            "asset_class": "equity",
            "quantity": 0,
            "cost_basis": 1.0,
            "currency": "USD",
        },
        headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_position_crypto_must_be_crypto_class(
    client: AsyncClient,
    make_token: Callable[..., str],
) -> None:
    """Cross-field validation: market=CRYPTO requires asset_class=crypto."""
    r = await client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={
            "ticker": "BTC",
            "market": "CRYPTO",
            "asset_class": "equity",
            "quantity": 1,
            "cost_basis": 100.0,
            "currency": "USD",
        },
        headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_position_scopes_by_user(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    mock_conn.execute = AsyncMock(return_value="DELETE 1")
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.delete(
            f"/positions/{POSITION_ID}",
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 204
    call_args = mock_conn.execute.await_args.args
    assert UUID(TEST_USER_ID) in call_args
    assert UUID(POSITION_ID) in call_args
