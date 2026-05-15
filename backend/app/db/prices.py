from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.db.pool import acquire_conn


async def fetch_cache_rows(keys: list[str]) -> list[dict[str, Any]]:
    if not keys:
        return []
    async with acquire_conn() as conn:
        rows = await conn.fetch(
            "SELECT key, value, expires_at FROM cache_kv "
            "WHERE key = ANY($1::text[]) AND expires_at > now()",
            keys,
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["value"], str):
            d["value"] = json.loads(d["value"])
        out.append(d)
    return out


async def write_cache_rows(rows: list[tuple[str, dict[str, Any], datetime]]) -> None:
    """Each row: (key, value_json, expires_at). Upserts on conflict."""
    if not rows:
        return
    async with acquire_conn() as conn:
        await conn.executemany(
            "INSERT INTO cache_kv (key, value, expires_at) "
            "VALUES ($1, $2::jsonb, $3) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "expires_at = EXCLUDED.expires_at",
            [(key, json.dumps(value), expires_at) for key, value, expires_at in rows],
        )


async def delete_cache_keys(keys: list[str]) -> None:
    if not keys:
        return
    async with acquire_conn() as conn:
        await conn.execute("DELETE FROM cache_kv WHERE key = ANY($1::text[])", keys)
