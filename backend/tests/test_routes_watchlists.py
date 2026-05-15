from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"
WL_ID = "00000000-0000-0000-0000-0000000000b1"


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
async def test_create_watchlist(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    mock_conn.fetchrow.return_value = {
        "id": UUID(WL_ID),
        "user_id": UUID(TEST_USER_ID),
        "name": "Movers",
        "created_at": "2026-05-15T00:00:00+00:00",
    }
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            "/watchlists",
            json={"name": "Movers"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 201
    assert r.json()["name"] == "Movers"


@pytest.mark.asyncio
async def test_list_watchlist_items(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    mock_conn.fetchrow.return_value = {"id": UUID(WL_ID)}  # ownership
    mock_conn.fetch.return_value = [
        {
            "watchlist_id": UUID(WL_ID),
            "ticker": "NVDA",
            "market": "US",
            "added_at": "2026-05-15T00:00:00+00:00",
            "notes": "AI play",
        },
    ]
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.get(
            f"/watchlists/{WL_ID}/items",
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["ticker"] == "NVDA"


@pytest.mark.asyncio
async def test_add_watchlist_item(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    mock_conn.fetchrow.side_effect = [
        {"id": UUID(WL_ID)},  # ownership
        {  # INSERT result
            "watchlist_id": UUID(WL_ID),
            "ticker": "NVDA",
            "market": "US",
            "added_at": "2026-05-15T00:00:00+00:00",
            "notes": "AI play",
        },
    ]
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.post(
            f"/watchlists/{WL_ID}/items",
            json={"ticker": "NVDA", "market": "US", "notes": "AI play"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 201
    assert r.json()["ticker"] == "NVDA"


@pytest.mark.asyncio
async def test_delete_watchlist_item(
    client: AsyncClient,
    make_token: Callable[..., str],
    mock_conn: Any,
) -> None:
    mock_conn.fetchrow.return_value = {"id": UUID(WL_ID)}  # ownership
    mock_conn.execute.return_value = "DELETE 1"
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = await client.delete(
            f"/watchlists/{WL_ID}/items/NVDA:US",
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_watchlist_routes_require_auth(client: AsyncClient) -> None:
    r1 = await client.get("/watchlists")
    r2 = await client.post("/watchlists", json={"name": "x"})
    assert r1.status_code == 401
    assert r2.status_code == 401
