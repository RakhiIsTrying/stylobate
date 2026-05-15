from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth import get_current_user
from app.db import watchlists as db
from app.models.watchlist import (
    WatchlistIn,
    WatchlistItemIn,
    WatchlistItemListOut,
    WatchlistItemOut,
    WatchlistListOut,
    WatchlistOut,
)

router = APIRouter(tags=["watchlists"])


@router.get("/watchlists", response_model=WatchlistListOut)
async def list_endpoint(
    user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistListOut:
    rows = await db.list_watchlists(user["sub"])
    return WatchlistListOut(watchlists=[WatchlistOut(**r) for r in rows])


@router.post(
    "/watchlists",
    response_model=WatchlistOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint(
    req: WatchlistIn,
    user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistOut:
    row = await db.create_watchlist(user["sub"], req.name)
    return WatchlistOut(**row)


@router.delete("/watchlists/{wl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    wl_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_watchlist(user["sub"], str(wl_id))
    if not ok:
        raise HTTPException(404, "Watchlist not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/watchlists/{wl_id}/items", response_model=WatchlistItemListOut)
async def list_items_endpoint(
    wl_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistItemListOut:
    if not await db.assert_watchlist_owned(user["sub"], str(wl_id)):
        raise HTTPException(404, "Watchlist not found")
    rows = await db.list_items(str(wl_id))
    return WatchlistItemListOut(items=[WatchlistItemOut(**r) for r in rows])


@router.post(
    "/watchlists/{wl_id}/items",
    response_model=WatchlistItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_item_endpoint(
    wl_id: UUID,
    req: WatchlistItemIn,
    user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistItemOut:
    if not await db.assert_watchlist_owned(user["sub"], str(wl_id)):
        raise HTTPException(404, "Watchlist not found")
    row = await db.insert_item(str(wl_id), req.ticker, req.market, req.notes)
    return WatchlistItemOut(**row)


@router.delete(
    "/watchlists/{wl_id}/items/{ticker_market}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_item_endpoint(
    wl_id: UUID,
    ticker_market: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    if not await db.assert_watchlist_owned(user["sub"], str(wl_id)):
        raise HTTPException(404, "Watchlist not found")
    if ":" not in ticker_market:
        raise HTTPException(422, "ticker_market must be 'TICKER:MARKET'")
    ticker, market = ticker_market.split(":", 1)
    if market not in ("US", "IN", "CRYPTO"):
        raise HTTPException(422, "invalid market")
    ok = await db.delete_item(str(wl_id), ticker.upper(), market)
    if not ok:
        raise HTTPException(404, "Item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
