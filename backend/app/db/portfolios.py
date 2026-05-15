from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.pool import acquire_conn

__all__ = [
    "acquire_conn",
    "assert_portfolio_owned",
    "create_portfolio",
    "delete_portfolio",
    "delete_position",
    "get_position_by_ticker",
    "insert_position",
    "list_portfolios",
    "list_positions",
    "rename_portfolio",
    "update_position",
]


async def list_positions(user_id: str, portfolio_id: str) -> list[dict[str, Any]]:
    async with acquire_conn() as conn:
        rows = await conn.fetch(
            "SELECT positions.id, portfolio_id, ticker, market, asset_class, "
            "quantity, cost_basis, currency, opened_at, positions.created_at "
            "FROM positions JOIN portfolios ON positions.portfolio_id = portfolios.id "
            "WHERE portfolio_id = $1 AND portfolios.user_id = $2 "
            "ORDER BY ticker ASC",
            UUID(portfolio_id), UUID(user_id),
        )
    return [dict(r) for r in rows]


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


async def assert_portfolio_owned(user_id: str, portfolio_id: str) -> bool:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM portfolios WHERE id = $1 AND user_id = $2",
            UUID(portfolio_id),
            UUID(user_id),
        )
    return row is not None


async def get_position_by_ticker(
    portfolio_id: str, ticker: str, market: str,
) -> dict[str, Any] | None:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id, portfolio_id, ticker, market, asset_class, quantity, "
            "cost_basis, currency, opened_at, created_at "
            "FROM positions WHERE portfolio_id = $1 AND ticker = $2 AND market = $3",
            UUID(portfolio_id),
            ticker,
            market,
        )
    return dict(row) if row else None


async def insert_position(
    portfolio_id: str,
    ticker: str,
    market: str,
    asset_class: str,
    quantity: Any,
    cost_basis: Any,
    currency: str,
    opened_at: Any,
) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO positions "
            "(portfolio_id, ticker, market, asset_class, quantity, cost_basis, "
            "currency, opened_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "RETURNING id, portfolio_id, ticker, market, asset_class, quantity, "
            "cost_basis, currency, opened_at, created_at",
            UUID(portfolio_id),
            ticker,
            market,
            asset_class,
            quantity,
            cost_basis,
            currency,
            opened_at,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def update_position(
    position_id: str,
    user_id: str,
    quantity: Any = None,
    cost_basis: Any = None,
    opened_at: Any = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    args: list[Any] = []
    for col, val in (
        ("quantity", quantity),
        ("cost_basis", cost_basis),
        ("opened_at", opened_at),
    ):
        if val is not None:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    if not sets:
        return None
    args.append(UUID(position_id))
    args.append(UUID(user_id))
    sql = (
        f"UPDATE positions SET {', '.join(sets)} "
        f"FROM portfolios p "
        f"WHERE positions.id = ${len(args) - 1} AND positions.portfolio_id = p.id "
        f"AND p.user_id = ${len(args)} "
        f"RETURNING positions.id, positions.portfolio_id, ticker, market, asset_class, "
        f"quantity, cost_basis, currency, opened_at, positions.created_at"
    )
    async with acquire_conn() as conn:
        row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None


async def delete_position(position_id: str, user_id: str) -> bool:
    async with acquire_conn() as conn:
        status_str = await conn.execute(
            "DELETE FROM positions USING portfolios "
            "WHERE positions.id = $1 AND positions.portfolio_id = portfolios.id "
            "AND portfolios.user_id = $2",
            UUID(position_id),
            UUID(user_id),
        )
    return bool(status_str.endswith("1"))
