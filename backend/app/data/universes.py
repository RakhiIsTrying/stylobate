from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
)
from app.db.prices import (
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=24)
_KEY = "universe:crypto:top100"

_COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
)

# Used when CoinGecko is unreachable. Keep this list small + stable.
_CRYPTO_BACKUP: list[dict[str, Any]] = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "USDT", "name": "Tether"},
    {"ticker": "BNB", "name": "BNB"},
    {"ticker": "SOL", "name": "Solana"},
    {"ticker": "XRP", "name": "XRP"},
    {"ticker": "USDC", "name": "USD Coin"},
    {"ticker": "ADA", "name": "Cardano"},
    {"ticker": "AVAX", "name": "Avalanche"},
    {"ticker": "DOGE", "name": "Dogecoin"},
]


def list_universe_names() -> list[str]:
    return ["sp500", "nifty500", "crypto"]


async def load_universe(name: str) -> list[dict[str, Any]]:
    """Return a flat list of constituent dicts. Raises ValueError on unknown."""
    if name == "crypto":
        return await _load_crypto_top100()
    if name == "sp500":
        return _load_static_universe("sp500.json")
    if name == "nifty500":
        return _load_static_universe("nifty500.json")
    raise ValueError(f"unknown universe: {name!r}")


def _load_static_universe(filename: str) -> list[dict[str, Any]]:
    path = (
        Path(__file__).parent.parent.parent / "data" / "universes" / filename
    )
    with path.open(encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise RuntimeError(f"{filename} did not contain a JSON array")
    return [dict(r) for r in rows]


async def _fetch_coingecko_top100() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_COINGECKO_URL)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError("CoinGecko returned non-array")
    return data


def _normalize_coingecko(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in raw:
        symbol = (r.get("symbol") or "").upper()
        if not symbol:
            continue
        out.append({
            "ticker": symbol,
            "name": r.get("name") or symbol,
            "sector": None,
            "currency": "USD",
            "asset_class": "crypto",
            "market": "CRYPTO",
            "market_cap": r.get("market_cap"),
        })
    return out


async def _load_crypto_top100() -> list[dict[str, Any]]:
    rows = await _fetch_cache_rows([_KEY])
    if rows:
        cached = rows[0]["value"]
        if isinstance(cached, list):
            return [dict(r) for r in cached]

    try:
        raw = await _fetch_coingecko_top100()
        normalized = _normalize_coingecko(raw)
    except Exception as e:
        logger.warning("CoinGecko fetch failed: %s — using backup list", e)
        return [
            {**r, "sector": None, "currency": "USD",
             "asset_class": "crypto", "market": "CRYPTO"}
            for r in _CRYPTO_BACKUP
        ]

    if not normalized:
        return [
            {**r, "sector": None, "currency": "USD",
             "asset_class": "crypto", "market": "CRYPTO"}
            for r in _CRYPTO_BACKUP
        ]

    expires = datetime.now(UTC) + _TTL
    try:
        await _write_cache_rows([(_KEY, normalized, expires)])  # type: ignore[list-item]
    except Exception as e:
        logger.warning("crypto universe cache write failed: %s", e)
    return normalized
