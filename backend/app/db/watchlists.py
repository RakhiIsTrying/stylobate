from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.pool import acquire_conn

__all__ = [
    "acquire_conn",
    "assert_watchlist_owned",
    "create_watchlist",
    "delete_item",
    "delete_watchlist",
    "insert_item",
    "list_items",
    "list_watchlists",
]


async def list_watchlists(user_id: str) -> list[dict[str, Any]]:
    async with acquire_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, name, created_at FROM watchlists "
            "WHERE user_id = $1 ORDER BY created_at ASC",
            UUID(user_id),
        )
    return [dict(r) for r in rows]


async def create_watchlist(user_id: str, name: str) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO watchlists (user_id, name) VALUES ($1, $2) "
            "RETURNING id, user_id, name, created_at",
            UUID(user_id),
            name,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_watchlist(user_id: str, wl_id: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM watchlists WHERE id = $1 AND user_id = $2",
            UUID(wl_id),
            UUID(user_id),
        )
    return bool(status == "DELETE 1")


async def assert_watchlist_owned(user_id: str, wl_id: str) -> bool:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM watchlists WHERE id = $1 AND user_id = $2",
            UUID(wl_id),
            UUID(user_id),
        )
    return row is not None


async def list_items(wl_id: str) -> list[dict[str, Any]]:
    async with acquire_conn() as conn:
        rows = await conn.fetch(
            "SELECT watchlist_id, ticker, market, added_at, notes "
            "FROM watchlist_items WHERE watchlist_id = $1 ORDER BY added_at DESC",
            UUID(wl_id),
        )
    return [dict(r) for r in rows]


async def insert_item(
    wl_id: str,
    ticker: str,
    market: str,
    notes: str | None,
) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO watchlist_items (watchlist_id, ticker, market, notes) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (watchlist_id, ticker, market) "
            "DO UPDATE SET notes = EXCLUDED.notes "
            "RETURNING watchlist_id, ticker, market, added_at, notes",
            UUID(wl_id),
            ticker,
            market,
            notes,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_item(wl_id: str, ticker: str, market: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM watchlist_items "
            "WHERE watchlist_id = $1 AND ticker = $2 AND market = $3",
            UUID(wl_id),
            ticker,
            market,
        )
    return bool(status == "DELETE 1")
