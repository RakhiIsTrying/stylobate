from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("SUPABASE_DB_URL (or DATABASE_URL) env var not set")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire_conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
