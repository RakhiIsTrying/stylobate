from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.data.prices_cache import Price

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_batch_prices_returns_keyed_dict(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    now = datetime(2026, 5, 15, 14, 23, 0)
    prices = {
        ("AAPL", "US"): Price(
            ticker="AAPL", market="US", price=189.42, currency="USD", as_of=now
        ),
        ("BTC", "CRYPTO"): Price(
            ticker="BTC", market="CRYPTO", price=80652.0, currency="USD", as_of=now
        ),
    }
    with patch("app.routes.prices.get_prices", AsyncMock(return_value=prices)):
        r = await client.get(
            "/prices?tickers=AAPL:US,BTC:CRYPTO",
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prices"]["AAPL:US"]["price"] == 189.42
    assert body["prices"]["BTC:CRYPTO"]["price"] == 80652.0


@pytest.mark.asyncio
async def test_batch_prices_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/prices?tickers=AAPL:US")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_prices_invalidates_and_refetches(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    invalidate_mock = AsyncMock()
    now = datetime(2026, 5, 15, 14, 30, 0)
    fresh = {
        ("AAPL", "US"): Price(
            ticker="AAPL", market="US", price=190.0, currency="USD", as_of=now
        ),
    }
    with patch("app.routes.prices.invalidate_prices", invalidate_mock), \
         patch("app.routes.prices.get_prices", AsyncMock(return_value=fresh)):
        r = await client.post(
            "/prices/refresh",
            json={"tickers": ["AAPL:US"]},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    invalidate_mock.assert_awaited_once_with([("AAPL", "US")])
    assert r.json()["prices"]["AAPL:US"]["price"] == 190.0


@pytest.mark.asyncio
async def test_batch_prices_rejects_bad_format(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get(
        "/prices?tickers=NOSEP", headers=_bearer(make_token(TEST_USER_ID))
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_batch_prices_rejects_bad_market(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get(
        "/prices?tickers=AAPL:EU", headers=_bearer(make_token(TEST_USER_ID))
    )
    assert r.status_code == 422
