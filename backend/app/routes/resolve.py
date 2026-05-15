from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents.ticker_resolver import TickerResolution, resolve_ticker
from app.core.auth import get_current_user

router = APIRouter(tags=["resolve"])

_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_cache: dict[str, tuple[float, TickerResolution]] = {}
_cache_lock = asyncio.Lock()


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)


@router.post("/resolve", response_model=TickerResolution)
async def resolve_endpoint(
    req: ResolveRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> TickerResolution:
    key = req.query.strip().lower()
    now = time.monotonic()
    async with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
    result = await resolve_ticker(req.query)
    async with _cache_lock:
        _cache[key] = (now, result)
    return result
