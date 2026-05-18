from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_load_static_universe_sp500() -> None:
    """SP500 JSON loads and returns Constituent-shaped dicts."""
    from app.data.universes import load_universe
    rows = await load_universe("sp500")
    assert len(rows) >= 100  # Wikipedia always has 500+ constituents
    sample = rows[0]
    assert "ticker" in sample
    assert "name" in sample
    assert sample["currency"] == "USD"
    assert sample["asset_class"] == "equity"
    assert sample["market"] == "US"


@pytest.mark.asyncio
async def test_load_static_universe_nifty500() -> None:
    from app.data.universes import load_universe
    rows = await load_universe("nifty500")
    assert len(rows) >= 100
    sample = rows[0]
    assert sample["currency"] == "INR"
    assert sample["asset_class"] == "equity"
    assert sample["market"] == "IN"
    assert sample["ticker"].endswith(".NS")


@pytest.mark.asyncio
async def test_load_universe_unknown_raises() -> None:
    from app.data.universes import load_universe
    with pytest.raises(ValueError, match="unknown universe"):
        await load_universe("nasdaq100")


@pytest.mark.asyncio
async def test_load_universe_crypto_cache_hit() -> None:
    now = datetime.now()
    cached_rows = [
        {"ticker": "BTC", "name": "Bitcoin", "sector": None,
         "currency": "USD", "asset_class": "crypto", "market": "CRYPTO",
         "market_cap": 1_500_000_000_000},
    ]
    rows = [{
        "key": "universe:crypto:top100",
        "value": cached_rows,
        "expires_at": now + timedelta(hours=12),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", AsyncMock()):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    assert len(result) == 1
    assert result[0]["ticker"] == "BTC"
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_load_universe_crypto_cache_miss_fetches_and_writes() -> None:
    fake_coingecko = [
        {"symbol": "btc", "name": "Bitcoin",
         "market_cap": 1_500_000_000_000},
        {"symbol": "eth", "name": "Ethereum",
         "market_cap": 400_000_000_000},
    ]
    fetch_mock = AsyncMock(return_value=fake_coingecko)
    write_mock = AsyncMock()
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", write_mock):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    assert [r["ticker"] for r in result] == ["BTC", "ETH"]
    assert all(r["currency"] == "USD" for r in result)
    assert all(r["asset_class"] == "crypto" for r in result)
    assert all(r["market"] == "CRYPTO" for r in result)
    fetch_mock.assert_awaited_once()
    write_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_universe_crypto_fallback_on_fetch_error() -> None:
    """CoinGecko 429 / network error returns the backup top-20 list."""
    fetch_mock = AsyncMock(side_effect=RuntimeError("429 rate limited"))
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", AsyncMock()):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    tickers = [r["ticker"] for r in result]
    assert "BTC" in tickers
    assert "ETH" in tickers
    assert len(result) >= 5  # at least the backup list


def test_list_universes_names() -> None:
    from app.data.universes import list_universe_names
    names = list_universe_names()
    assert set(names) == {"sp500", "nifty500", "crypto"}
