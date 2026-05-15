from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.pool import acquire_conn

__all__ = [
    "acquire_conn",
    "create_portfolio",
    "delete_portfolio",
    "list_portfolios",
    "rename_portfolio",
]


async def list_portfolios(user_id: str) -> list[dict[str, Any]]:
    async with acquire_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, name, base_currency, created_at, updated_at "
            "FROM portfolios WHERE user_id = $1 ORDER BY created_at ASC",
            UUID(user_id),
        )
    return [dict(r) for r in rows]


async def create_portfolio(user_id: str, name: str, base_currency: str) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO portfolios (user_id, name, base_currency) "
            "VALUES ($1, $2, $3) "
            "RETURNING id, user_id, name, base_currency, created_at, updated_at",
            UUID(user_id),
            name,
            base_currency,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_portfolio(user_id: str, portfolio_id: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM portfolios WHERE id = $1 AND user_id = $2",
            UUID(portfolio_id),
            UUID(user_id),
        )
    return bool(status.endswith("1"))


async def rename_portfolio(
    user_id: str,
    portfolio_id: str,
    name: str | None,
    base_currency: str | None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        args.append(name)
        sets.append(f"name = ${len(args)}")
    if base_currency is not None:
        args.append(base_currency)
        sets.append(f"base_currency = ${len(args)}")
    if not sets:
        return None
    args.append(UUID(portfolio_id))
    args.append(UUID(user_id))
    sql = (
        f"UPDATE portfolios SET {', '.join(sets)}, updated_at = now() "
        f"WHERE id = ${len(args) - 1} AND user_id = ${len(args)} "
        f"RETURNING id, user_id, name, base_currency, created_at, updated_at"
    )
    async with acquire_conn() as conn:
        row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None
