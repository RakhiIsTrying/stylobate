from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.data.prices_cache import Price, get_prices, invalidate_prices

router = APIRouter(tags=["prices"])

_VALID_MARKETS = {"US", "IN", "CRYPTO"}


def _parse_tickers(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            raise HTTPException(422, f"tickers must be 'TICKER:MARKET' (got {part!r})")
        t, m = part.split(":", 1)
        t, m = t.upper(), m.upper()
        if m not in _VALID_MARKETS:
            raise HTTPException(422, f"invalid market: {m}")
        pairs.append((t, m))
    return pairs


def _price_json(p: Price) -> dict[str, Any]:
    return {
        "price": p.price,
        "currency": p.currency,
        "as_of": p.as_of.isoformat(),
    }


@router.get("/prices")
async def batch_prices(
    tickers: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    pairs = _parse_tickers(tickers)
    prices = await get_prices(pairs)
    out = {f"{t}:{m}": _price_json(p) for (t, m), p in prices.items()}
    return {"prices": out, "prices_partial": len(out) != len(pairs)}


class RefreshRequest(BaseModel):
    tickers: list[str]


@router.post("/prices/refresh")
async def refresh_prices(
    req: RefreshRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    pairs = _parse_tickers(",".join(req.tickers))
    await invalidate_prices(pairs)
    prices = await get_prices(pairs)
    out = {f"{t}:{m}": _price_json(p) for (t, m), p in prices.items()}
    return {"prices": out, "prices_partial": len(out) != len(pairs)}
