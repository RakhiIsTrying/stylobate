from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


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
async def test_list_portfolios_empty(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.get(
            "/portfolios",
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 200
    assert r.json() == {"portfolios": []}


@pytest.mark.asyncio
async def test_create_portfolio_persists(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    portfolio_id = "11111111-1111-1111-1111-111111111111"
    row = {
        "id": UUID(portfolio_id),
        "user_id": UUID(TEST_USER_ID),
        "name": "Growth",
        "base_currency": "USD",
        "created_at": "2026-05-15T00:00:00+00:00",
        "updated_at": "2026-05-15T00:00:00+00:00",
    }
    mock_conn.fetchrow = AsyncMock(return_value=row)

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            "/portfolios",
            json={"name": "Growth", "base_currency": "usd"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 201
    body = r.json()
    assert body["id"] == portfolio_id
    assert body["user_id"] == TEST_USER_ID
    assert body["name"] == "Growth"
    assert body["base_currency"] == "USD"

    # Verify the user UUID is scoped into the INSERT args.
    call_args = mock_conn.fetchrow.await_args.args
    assert UUID(TEST_USER_ID) in call_args
    assert "Growth" in call_args
    assert "USD" in call_args


@pytest.mark.asyncio
async def test_create_portfolio_rejects_bad_currency(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            "/portfolios",
            json={"name": "Bad", "base_currency": "BITCOIN"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_portfolio_scopes_by_user(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    portfolio_id = "22222222-2222-2222-2222-222222222222"
    mock_conn.execute = AsyncMock(return_value="DELETE 1")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.delete(
            f"/portfolios/{portfolio_id}",
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 204
    call_args = mock_conn.execute.await_args.args
    assert UUID(TEST_USER_ID) in call_args
    assert UUID(portfolio_id) in call_args


@pytest.mark.asyncio
async def test_routes_require_auth(client: AsyncClient) -> None:
    r1 = await client.get("/portfolios")
    assert r1.status_code == 401
    r2 = await client.post("/portfolios", json={"name": "X", "base_currency": "USD"})
    assert r2.status_code == 401
