# backend/tests/test_routes_resolve.py
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.agents.ticker_resolver import TickerResolution


@pytest.mark.asyncio
async def test_resolve_returns_typed_resolution(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    user_id = "00000000-0000-0000-0000-000000000001"
    fake_resolution = TickerResolution(
        ticker="AAPL",
        name="Apple Inc.",
        market="US",
        asset_class="equity",
        confidence=0.95,
        candidates=[],
    )

    from app.routes import resolve as resolve_routes

    resolve_routes._cache.clear()

    with patch(
        "app.routes.resolve.resolve_ticker",
        new=AsyncMock(return_value=fake_resolution),
    ):
        response = await client.post(
            "/resolve",
            json={"query": "Apple"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAPL"
    assert body["name"] == "Apple Inc."
    assert body["market"] == "US"
    assert body["asset_class"] == "equity"
    assert body["confidence"] == 0.95
    assert body["candidates"] == []


@pytest.mark.asyncio
async def test_resolve_requires_auth(client: AsyncClient) -> None:
    response = await client.post("/resolve", json={"query": "Apple"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_resolve_caches_repeat_queries(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    user_id = "00000000-0000-0000-0000-000000000002"
    fake_resolution = TickerResolution(
        ticker="AAPL",
        name="Apple Inc.",
        market="US",
        asset_class="equity",
        confidence=0.95,
        candidates=[],
    )

    from app.routes import resolve as resolve_routes

    resolve_routes._cache.clear()

    mock_resolve = AsyncMock(return_value=fake_resolution)
    with patch("app.routes.resolve.resolve_ticker", new=mock_resolve):
        headers = {"Authorization": f"Bearer {make_token(user_id)}"}
        first = await client.post("/resolve", json={"query": "Apple"}, headers=headers)
        second = await client.post("/resolve", json={"query": "Apple"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert mock_resolve.await_count == 1
