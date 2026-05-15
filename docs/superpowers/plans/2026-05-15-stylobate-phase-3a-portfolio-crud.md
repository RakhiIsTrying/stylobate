# Stylobate — Phase 3A: Portfolio & Watchlist CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the portfolio + watchlist CRUD UI and backend so users can manage holdings end-to-end. No agents (those land in 3B/3C).

**Architecture:** New FastAPI route modules under `app/routes/` (portfolios, positions, watchlists, prices, resolve). Pydantic models in `app/models/`. Async SQL helpers in `app/db/` using `asyncpg` connections that scope each query to `user_id`. A new `app/data/prices_cache.py` module wraps the existing `cache_kv` table with 15-min TTL. Frontend adds a single `/portfolio` page with two tabs (Holdings, Watchlist) and ~9 components. Playwright covers the critical browser paths.

**Tech Stack:** Existing — FastAPI, Pydantic v2, asyncpg (Supabase Postgres), Next.js (App Router), Tailwind, shadcn-style components, yfinance. New: `@playwright/test` (frontend e2e only).

**Spec reference:** `docs/superpowers/specs/2026-05-15-stylobate-phase-3a-portfolio-crud-design.md` — every section of that doc maps to one or more tasks below.

**Frontend caveat:** The frontend AGENTS.md warns "this is NOT the Next.js you know" — before writing any new app-router code, check `frontend/node_modules/next/dist/docs/` for the current API and heed deprecation notices.

---

## File map

```
backend/
  app/
    routes/
      resolve.py             NEW — POST /resolve (T1)
      portfolios.py          NEW — portfolios + positions CRUD (T2, T3)
      watchlists.py          NEW — watchlists + items CRUD (T4)
      prices.py              NEW — batch GET + refresh (T6)
    models/
      portfolio.py           NEW — PortfolioIn/Out, PositionIn/Out, CohortSummary (T2-T3)
      watchlist.py           NEW — WatchlistIn/Out, WatchlistItemIn/Out (T4)
    db/
      pool.py                NEW — asyncpg pool factory (T2)
      portfolios.py          NEW — portfolio + position SQL helpers (T2-T3)
      watchlists.py          NEW — watchlist SQL helpers (T4)
      prices.py              NEW — cache_kv access (T5)
    data/
      prices_cache.py        NEW — get_prices, invalidate_prices (T5)
    agents/
      ticker_resolver.py     MODIFY — add optional cache hook for /resolve (T1)
    main.py                  MODIFY — register routers (T1, T2, T4, T6)
    routes/chat.py           MODIFY — call shared resolver path (T1)
  tests/
    test_routes_resolve.py        NEW (T1)
    test_routes_portfolios.py     NEW (T2)
    test_routes_positions.py      NEW (T3)
    test_routes_watchlists.py     NEW (T4)
    test_prices_cache.py          NEW (T5)
    test_routes_prices.py         NEW (T6)
    test_routes_e2e_portfolio.py  NEW (T6)
  pyproject.toml             MODIFY — +asyncpg (T2)

frontend/
  app/
    portfolio/
      page.tsx               NEW — main page with tabs (T7)
      layout.tsx             NEW — auth gate (T7)
  components/
    portfolio-tab.tsx        NEW — Holdings tab content (T8)
    watchlist-tab.tsx        NEW — Watchlist tab content (T10)
    portfolio-selector.tsx   NEW (T8)
    cohort-card.tsx          NEW (T8)
    portfolio-table.tsx      NEW — grouped table (T8)
    add-position-modal.tsx   NEW (T9)
    edit-position-modal.tsx  NEW (T9)
    ticker-autocomplete.tsx  NEW — shared by both modals + watchlist add (T9)
    refresh-button.tsx       NEW (T8)
    watchlist-selector.tsx   NEW (T10)
    watchlist-table.tsx      NEW (T10)
  lib/
    api-resolve.ts           NEW (T7)
    api-portfolios.ts        NEW (T7)
    api-watchlists.ts        NEW (T7)
    api-prices.ts            NEW (T7)
    api-base.ts              NEW — shared fetch with JWT (T7)
  components/sidebar.tsx     MODIFY — add /portfolio link (T7)
  e2e/
    portfolio.spec.ts        NEW — 3 Playwright tests (T11)
  playwright.config.ts       NEW (T11)
  package.json               MODIFY — +@playwright/test (T11)
```

---

## Glossary

- **Cohort** = `(currency, asset_class_group)` where `asset_class_group` is `equity_etf` for {equity, etf} and `crypto` for {crypto}. Display labels: "USD (Equities & ETFs)", "INR (Equities & ETFs)", "CRYPTO (USD)".
- **Position UPSERT**: `POST /portfolios/{id}/positions` on an existing `(portfolio_id, ticker, market)` recomputes weighted-average cost basis and sums quantity instead of inserting a duplicate.
- **Live price**: `fetch_ticker_info(ticker, market=market).last_price` from the Phase 1 yfinance adapter, cached in `cache_kv` with key `price:{ticker}:{market}` and 15-min TTL.

---

## Pre-flight check

Before starting Task 1, verify the current state is what the plan expects:

- [ ] **Step P1: Confirm baseline tests pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -q
```

Expected: 92 passing (Phase 2C end state).

- [ ] **Step P2: Confirm baseline lint + types clean**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests
```

Expected: `All checks passed!` + `Success: no issues found in 58 source files`.

If either is failing, do not proceed — pause and surface to the controller.

---

## Task 1: Standalone /resolve endpoint

**Files:**
- Create: `backend/app/routes/resolve.py`
- Create: `backend/tests/test_routes_resolve.py`
- Modify: `backend/app/main.py` (register router)
- Modify: `backend/app/agents/ticker_resolver.py` (add a tiny in-memory cache decorator)

**Goal:** Expose `POST /resolve` that the Add-position modal can call without starting a chat turn. Reuses the existing `resolve_ticker` function from Phase 1 with a small in-memory cache (24h LRU).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routes_resolve.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents.ticker_resolver import TickerResolution


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    """Reuse Phase 0 helper if it exists, else inline a stubbed JWT."""
    from tests.helpers.auth import bearer_for_test_user
    return bearer_for_test_user()


def test_resolve_returns_typed_resolution(client: TestClient) -> None:
    fake = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.99,
    )
    with patch(
        "app.routes.resolve.resolve_ticker",
        new=AsyncMock(return_value=fake),
    ):
        r = client.post(
            "/resolve",
            json={"query": "Apple"},
            headers=_auth_headers(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["market"] == "US"
    assert body["asset_class"] == "equity"
    assert body["confidence"] == 0.99


def test_resolve_requires_auth(client: TestClient) -> None:
    r = client.post("/resolve", json={"query": "Apple"})
    assert r.status_code == 401


def test_resolve_caches_repeat_queries(client: TestClient) -> None:
    """Same query within 24h should not re-call the underlying resolver."""
    fake = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.99,
    )
    mock = AsyncMock(return_value=fake)
    with patch("app.routes.resolve.resolve_ticker", new=mock):
        r1 = client.post("/resolve", json={"query": "Apple"}, headers=_auth_headers())
        r2 = client.post("/resolve", json={"query": "Apple"}, headers=_auth_headers())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert mock.await_count == 1, "second call should be served from cache"
```

If `tests/helpers/auth.py` doesn't exist with `bearer_for_test_user`, look at how `test_routes_chat.py` (or whichever Phase 0/1 test exercises auth) handles it and follow the same pattern. If the existing tests bypass auth via a FastAPI dependency override, do the same here.

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_resolve.py -v
```

Expected: `ModuleNotFoundError` on `app.routes.resolve` (or 404 if the import is lazy).

- [ ] **Step 3: Create the route module**

Create `backend/app/routes/resolve.py`:

```python
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
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, find the existing `app.include_router(...)` calls and add:

```python
from app.routes import resolve as resolve_routes
app.include_router(resolve_routes.router)
```

If the existing routers use a `/api` prefix or similar, match that pattern.

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_resolve.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 95 passing (92 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/resolve.py backend/app/main.py backend/tests/test_routes_resolve.py && git commit -m "feat(backend): standalone POST /resolve endpoint (24h in-memory cache)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: asyncpg pool + Pydantic models + /portfolios CRUD

**Files:**
- Modify: `backend/pyproject.toml` (add asyncpg)
- Create: `backend/app/db/pool.py`
- Create: `backend/app/models/portfolio.py`
- Create: `backend/app/db/portfolios.py`
- Create: `backend/app/routes/portfolios.py`
- Create: `backend/tests/test_routes_portfolios.py`
- Modify: `backend/app/main.py` (register router; close pool on shutdown)

**Goal:** Get the portfolios CRUD working end-to-end with auth. Establishes the DB-access pattern (asyncpg pool, per-request connection, `user_id` scoping) that the rest of the backend tasks reuse.

- [ ] **Step 1: Add asyncpg dependency**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv add asyncpg
```

Verify `asyncpg` shows up under `[project.dependencies]` in `pyproject.toml`.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_routes_portfolios.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers.auth import bearer_for_test_user, TEST_USER_ID


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


@pytest.fixture
def mock_conn() -> Any:
    """Return a MagicMock that mimics an asyncpg connection."""
    conn = MagicMock()
    conn.fetch = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    return conn


def test_list_portfolios_empty(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetch.return_value = []
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.get("/portfolios", headers=bearer_for_test_user())
    assert r.status_code == 200
    assert r.json() == {"portfolios": []}


def test_create_portfolio_persists(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetchrow.return_value = {
        "id": "00000000-0000-0000-0000-000000000001",
        "user_id": TEST_USER_ID,
        "name": "My Portfolio",
        "base_currency": "USD",
        "created_at": "2026-05-15T00:00:00+00:00",
        "updated_at": "2026-05-15T00:00:00+00:00",
    }
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.post(
            "/portfolios",
            json={"name": "My Portfolio", "base_currency": "USD"},
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "00000000-0000-0000-0000-000000000001"
    assert body["name"] == "My Portfolio"
    # SQL must scope by user_id from JWT
    call_args = mock_conn.fetchrow.await_args
    assert TEST_USER_ID in call_args.args


def test_create_portfolio_rejects_bad_currency(client: TestClient) -> None:
    r = client.post(
        "/portfolios",
        json={"name": "Bad", "base_currency": "BITCOIN"},
        headers=bearer_for_test_user(),
    )
    assert r.status_code == 422


def test_delete_portfolio_scopes_by_user(client: TestClient, mock_conn: Any) -> None:
    mock_conn.execute.return_value = "DELETE 1"
    pid = "00000000-0000-0000-0000-000000000001"
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.delete(f"/portfolios/{pid}", headers=bearer_for_test_user())
    assert r.status_code == 204
    call_args = mock_conn.execute.await_args
    assert TEST_USER_ID in call_args.args
    assert pid in call_args.args


def test_routes_require_auth(client: TestClient) -> None:
    assert client.get("/portfolios").status_code == 401
    assert client.post("/portfolios", json={"name": "x", "base_currency": "USD"}).status_code == 401


# Helper to wrap a mock conn in an async context manager
class _async_cm:  # noqa: N801 — fixture-only helper
    def __init__(self, value: Any) -> None:
        self._value = value
    async def __aenter__(self) -> Any:
        return self._value
    async def __aexit__(self, *args: Any) -> None:
        pass
```

- [ ] **Step 3: Confirm test fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_portfolios.py -v
```

Expected: `ModuleNotFoundError` on `app.db.portfolios` or `app.routes.portfolios`.

- [ ] **Step 4: Add the asyncpg pool factory**

Create `backend/app/db/pool.py`:

```python
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

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
```

The Supabase project URL needs to be reachable via a Postgres DSN. The standard Supabase form is `postgresql://postgres:<password>@db.<projectref>.supabase.co:5432/postgres`. Add `SUPABASE_DB_URL=...` to `backend/.env` (the user's password comes from Supabase dashboard → Project Settings → Database; if the password isn't already in `.env`, ask the controller).

- [ ] **Step 5: Add Pydantic models**

Create `backend/app/models/portfolio.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Market = Literal["US", "IN", "CRYPTO"]
AssetClass = Literal["equity", "etf", "crypto"]


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(min_length=3, max_length=3)

    @field_validator("base_currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("base_currency must be a 3-letter ISO code")
        return v


class PortfolioOut(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    base_currency: str
    created_at: datetime
    updated_at: datetime


class PortfolioListOut(BaseModel):
    portfolios: list[PortfolioOut]
```

- [ ] **Step 6: Add SQL helpers**

Create `backend/app/db/portfolios.py`:

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.pool import acquire_conn

__all__ = ["acquire_conn", "list_portfolios", "create_portfolio", "delete_portfolio", "rename_portfolio"]


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
            "VALUES ($1, $2, $3) RETURNING id, user_id, name, base_currency, created_at, updated_at",
            UUID(user_id), name, base_currency,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_portfolio(user_id: str, portfolio_id: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM portfolios WHERE id = $1 AND user_id = $2",
            UUID(portfolio_id), UUID(user_id),
        )
    return status.endswith("1")


async def rename_portfolio(
    user_id: str, portfolio_id: str, name: str | None, base_currency: str | None,
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
```

- [ ] **Step 7: Add the route module**

Create `backend/app/routes/portfolios.py`:

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db import portfolios as db
from app.models.portfolio import PortfolioIn, PortfolioListOut, PortfolioOut

router = APIRouter(tags=["portfolios"])


@router.get("/portfolios", response_model=PortfolioListOut)
async def list_endpoint(user: dict[str, Any] = Depends(get_current_user)) -> PortfolioListOut:
    rows = await db.list_portfolios(user["sub"])
    return PortfolioListOut(portfolios=[PortfolioOut(**r) for r in rows])


@router.post("/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    req: PortfolioIn, user: dict[str, Any] = Depends(get_current_user),
) -> PortfolioOut:
    row = await db.create_portfolio(user["sub"], req.name, req.base_currency)
    return PortfolioOut(**row)


class PortfolioPatch(BaseModel):
    name: str | None = None
    base_currency: str | None = None


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def patch_endpoint(
    portfolio_id: UUID,
    req: PortfolioPatch,
    user: dict[str, Any] = Depends(get_current_user),
) -> PortfolioOut:
    row = await db.rename_portfolio(
        user["sub"], str(portfolio_id), req.name, req.base_currency,
    )
    if row is None:
        raise HTTPException(404, "Portfolio not found")
    return PortfolioOut(**row)


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    portfolio_id: UUID, user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_portfolio(user["sub"], str(portfolio_id))
    if not ok:
        raise HTTPException(404, "Portfolio not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 8: Register router + close pool on shutdown**

In `backend/app/main.py`:

```python
from app.routes import portfolios as portfolios_routes
from app.db.pool import close_pool

app.include_router(portfolios_routes.router)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()
```

If the project already uses a lifespan context manager, integrate `close_pool` into that instead of `on_event` (which is deprecated in newer FastAPI).

- [ ] **Step 9: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_portfolios.py -v
```

Expected: 5 passed.

- [ ] **Step 10: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 100 passing (95 prior + 5 new).

- [ ] **Step 11: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add -A && git commit -m "feat(backend): /portfolios CRUD with asyncpg pool, JWT-scoped queries

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: /positions CRUD with UPSERT on duplicate ticker

**Files:**
- Modify: `backend/app/models/portfolio.py` (add Position models)
- Modify: `backend/app/db/portfolios.py` (add position helpers)
- Modify: `backend/app/routes/portfolios.py` (add position routes)
- Create: `backend/tests/test_routes_positions.py`

**Goal:** POST/PATCH/DELETE for positions, with UPSERT-on-duplicate-ticker behaviour that recomputes weighted-average cost basis.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routes_positions.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers.auth import bearer_for_test_user, TEST_USER_ID


PORTFOLIO_ID = "00000000-0000-0000-0000-000000000001"
POSITION_ID = "00000000-0000-0000-0000-0000000000a1"


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.fixture
def mock_conn() -> Any:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_async_cm(None))
    return conn


def test_add_new_position(client: TestClient, mock_conn: Any) -> None:
    # No existing position with that ticker -> INSERT path
    mock_conn.fetchrow.side_effect = [
        # ownership check on portfolio
        {"id": PORTFOLIO_ID},
        # existing-position lookup (none)
        None,
        # INSERT returning row
        {
            "id": POSITION_ID, "portfolio_id": PORTFOLIO_ID, "ticker": "AAPL",
            "market": "US", "asset_class": "equity", "quantity": 50,
            "cost_basis": 175.0, "currency": "USD", "opened_at": "2024-03-15",
            "created_at": "2026-05-15T00:00:00+00:00",
        },
    ]
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.post(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            json={"ticker": "AAPL", "market": "US", "asset_class": "equity",
                  "quantity": 50, "cost_basis": 175.0, "currency": "USD",
                  "opened_at": "2024-03-15"},
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 201
    assert r.json()["quantity"] == 50


def test_add_position_upserts_weighted_avg(client: TestClient, mock_conn: Any) -> None:
    """Existing AAPL @ 50 shares, $175. Add 50 more @ $185 -> 100 shares @ $180."""
    mock_conn.fetchrow.side_effect = [
        {"id": PORTFOLIO_ID},  # ownership
        {  # existing position
            "id": POSITION_ID, "portfolio_id": PORTFOLIO_ID, "ticker": "AAPL",
            "market": "US", "asset_class": "equity", "quantity": 50,
            "cost_basis": 175.0, "currency": "USD", "opened_at": "2024-03-15",
            "created_at": "2026-05-15T00:00:00+00:00",
        },
        {  # UPDATE returning new state
            "id": POSITION_ID, "portfolio_id": PORTFOLIO_ID, "ticker": "AAPL",
            "market": "US", "asset_class": "equity", "quantity": 100,
            "cost_basis": 180.0, "currency": "USD", "opened_at": "2024-03-15",
            "created_at": "2026-05-15T00:00:00+00:00",
        },
    ]
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.post(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            json={"ticker": "AAPL", "market": "US", "asset_class": "equity",
                  "quantity": 50, "cost_basis": 185.0, "currency": "USD",
                  "opened_at": "2025-01-10"},
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 200  # 200 for UPSERT-update
    body = r.json()
    assert body["quantity"] == 100
    assert body["cost_basis"] == 180.0


def test_add_position_validates_market(client: TestClient) -> None:
    r = client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={"ticker": "AAPL", "market": "EU", "asset_class": "equity",
              "quantity": 1, "cost_basis": 1.0, "currency": "USD"},
        headers=bearer_for_test_user(),
    )
    assert r.status_code == 422


def test_add_position_rejects_zero_quantity(client: TestClient) -> None:
    r = client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={"ticker": "AAPL", "market": "US", "asset_class": "equity",
              "quantity": 0, "cost_basis": 1.0, "currency": "USD"},
        headers=bearer_for_test_user(),
    )
    assert r.status_code == 422


def test_add_position_crypto_must_be_crypto_class(client: TestClient) -> None:
    r = client.post(
        f"/portfolios/{PORTFOLIO_ID}/positions",
        json={"ticker": "BTC", "market": "CRYPTO", "asset_class": "equity",
              "quantity": 1, "cost_basis": 100.0, "currency": "USD"},
        headers=bearer_for_test_user(),
    )
    assert r.status_code == 422


def test_delete_position_scopes_by_user(client: TestClient, mock_conn: Any) -> None:
    mock_conn.execute.return_value = "DELETE 1"
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.delete(f"/positions/{POSITION_ID}", headers=bearer_for_test_user())
    assert r.status_code == 204
    call = mock_conn.execute.await_args
    assert TEST_USER_ID in call.args
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_positions.py -v
```

Expected: failures (routes don't exist; validators don't exist).

- [ ] **Step 3: Extend the Pydantic models**

Append to `backend/app/models/portfolio.py`:

```python
from datetime import date
from decimal import Decimal


class PositionIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    market: Market
    asset_class: AssetClass
    quantity: Decimal = Field(gt=0)
    cost_basis: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    opened_at: date | None = None

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper()

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code")
        return v

    @field_validator("asset_class")
    @classmethod
    def crypto_market_requires_crypto_class(cls, v: AssetClass, info: Any) -> AssetClass:
        # Field-level validation; cross-field validation done by model_validator below
        return v

    def model_post_init(self, _ctx: Any) -> None:  # pragma: no cover (trivial)
        if self.market == "CRYPTO" and self.asset_class != "crypto":
            raise ValueError("market=CRYPTO requires asset_class=crypto")
        if self.asset_class == "crypto" and self.market != "CRYPTO":
            raise ValueError("asset_class=crypto requires market=CRYPTO")


class PositionPatch(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    cost_basis: Decimal | None = Field(default=None, ge=0)
    opened_at: date | None = None


class PositionOut(BaseModel):
    id: UUID
    portfolio_id: UUID
    ticker: str
    market: Market
    asset_class: AssetClass
    quantity: Decimal
    cost_basis: Decimal | None
    currency: str
    opened_at: date | None
    created_at: datetime
```

(`Any` is already imported at top.)

- [ ] **Step 4: Extend the SQL helpers**

Append to `backend/app/db/portfolios.py`:

```python
async def assert_portfolio_owned(user_id: str, portfolio_id: str) -> bool:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM portfolios WHERE id = $1 AND user_id = $2",
            UUID(portfolio_id), UUID(user_id),
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
            UUID(portfolio_id), ticker, market,
        )
    return dict(row) if row else None


async def insert_position(
    portfolio_id: str, ticker: str, market: str, asset_class: str,
    quantity: Any, cost_basis: Any, currency: str, opened_at: Any,
) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO positions "
            "(portfolio_id, ticker, market, asset_class, quantity, cost_basis, currency, opened_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "RETURNING id, portfolio_id, ticker, market, asset_class, quantity, "
            "cost_basis, currency, opened_at, created_at",
            UUID(portfolio_id), ticker, market, asset_class,
            quantity, cost_basis, currency, opened_at,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def update_position(
    position_id: str, user_id: str,
    quantity: Any = None, cost_basis: Any = None, opened_at: Any = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    args: list[Any] = []
    for col, val in (("quantity", quantity), ("cost_basis", cost_basis), ("opened_at", opened_at)):
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
        f"WHERE positions.id = ${len(args) - 1} AND positions.portfolio_id = p.id AND p.user_id = ${len(args)} "
        f"RETURNING positions.id, positions.portfolio_id, ticker, market, asset_class, quantity, "
        f"cost_basis, currency, opened_at, positions.created_at"
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
            UUID(position_id), UUID(user_id),
        )
    return status_str.endswith("1")
```

- [ ] **Step 5: Add the routes**

Append to `backend/app/routes/portfolios.py`:

```python
from decimal import Decimal
from fastapi import Path
from app.models.portfolio import PositionIn, PositionOut, PositionPatch


@router.post("/portfolios/{portfolio_id}/positions", response_model=PositionOut)
async def add_position(
    portfolio_id: UUID,
    req: PositionIn,
    response: Response,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionOut:
    if not await db.assert_portfolio_owned(user["sub"], str(portfolio_id)):
        raise HTTPException(404, "Portfolio not found")
    existing = await db.get_position_by_ticker(str(portfolio_id), req.ticker, req.market)
    if existing is None:
        row = await db.insert_position(
            str(portfolio_id), req.ticker, req.market, req.asset_class,
            req.quantity, req.cost_basis, req.currency, req.opened_at,
        )
        response.status_code = status.HTTP_201_CREATED
        return PositionOut(**row)

    # UPSERT path: weighted-average cost basis, sum of quantities, earliest opened_at
    old_qty = Decimal(str(existing["quantity"]))
    old_basis = Decimal(str(existing["cost_basis"] or 0))
    new_qty = old_qty + req.quantity
    new_basis = (old_qty * old_basis + req.quantity * req.cost_basis) / new_qty
    earliest = min(
        d for d in (existing.get("opened_at"), req.opened_at) if d is not None
    ) if (existing.get("opened_at") or req.opened_at) else None
    row = await db.update_position(
        existing["id"], user["sub"], quantity=new_qty, cost_basis=new_basis,
        opened_at=earliest,
    )
    if row is None:
        raise HTTPException(404, "Position not found")
    return PositionOut(**row)


@router.patch("/positions/{position_id}", response_model=PositionOut)
async def patch_position(
    position_id: UUID, req: PositionPatch,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionOut:
    row = await db.update_position(
        str(position_id), user["sub"],
        quantity=req.quantity, cost_basis=req.cost_basis, opened_at=req.opened_at,
    )
    if row is None:
        raise HTTPException(404, "Position not found")
    return PositionOut(**row)


@router.delete("/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_position_endpoint(
    position_id: UUID, user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_position(str(position_id), user["sub"])
    if not ok:
        raise HTTPException(404, "Position not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 6: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_positions.py -v
```

Expected: 6 passed.

- [ ] **Step 7: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 106 passing (100 prior + 6 new).

- [ ] **Step 8: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add -A && git commit -m "feat(backend): /positions CRUD with UPSERT-on-duplicate (weighted-avg basis)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Watchlists + items CRUD

**Files:**
- Create: `backend/app/models/watchlist.py`
- Create: `backend/app/db/watchlists.py`
- Create: `backend/app/routes/watchlists.py`
- Create: `backend/tests/test_routes_watchlists.py`
- Modify: `backend/app/main.py` (register router)

**Goal:** Same shape as portfolios, simpler payload (no qty/cost). Items are keyed by `(watchlist_id, ticker, market)` per the existing schema PK.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routes_watchlists.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers.auth import bearer_for_test_user, TEST_USER_ID

WL_ID = "00000000-0000-0000-0000-0000000000b1"


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.fixture
def mock_conn() -> Any:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    return conn


def test_create_watchlist(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetchrow.return_value = {
        "id": WL_ID, "user_id": TEST_USER_ID, "name": "Movers",
        "created_at": "2026-05-15T00:00:00+00:00",
    }
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.post("/watchlists", json={"name": "Movers"}, headers=bearer_for_test_user())
    assert r.status_code == 201
    assert r.json()["name"] == "Movers"


def test_list_watchlist_items(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetchrow.return_value = {"id": WL_ID}  # ownership
    mock_conn.fetch.return_value = [
        {"watchlist_id": WL_ID, "ticker": "NVDA", "market": "US",
         "added_at": "2026-05-15T00:00:00+00:00", "notes": "AI play"},
    ]
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.get(f"/watchlists/{WL_ID}/items", headers=bearer_for_test_user())
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["ticker"] == "NVDA"


def test_add_watchlist_item(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetchrow.side_effect = [
        {"id": WL_ID},  # ownership
        {  # INSERT
            "watchlist_id": WL_ID, "ticker": "NVDA", "market": "US",
            "added_at": "2026-05-15T00:00:00+00:00", "notes": "AI play",
        },
    ]
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.post(
            f"/watchlists/{WL_ID}/items",
            json={"ticker": "NVDA", "market": "US", "notes": "AI play"},
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 201
    assert r.json()["ticker"] == "NVDA"


def test_delete_watchlist_item(client: TestClient, mock_conn: Any) -> None:
    mock_conn.fetchrow.return_value = {"id": WL_ID}  # ownership
    mock_conn.execute.return_value = "DELETE 1"
    with patch("app.db.watchlists.acquire_conn", return_value=_async_cm(mock_conn)):
        r = client.delete(
            f"/watchlists/{WL_ID}/items/NVDA:US",
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 204


def test_watchlist_routes_require_auth(client: TestClient) -> None:
    assert client.get("/watchlists").status_code == 401
    assert client.post("/watchlists", json={"name": "x"}).status_code == 401
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_watchlists.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Pydantic models**

Create `backend/app/models/watchlist.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Market = Literal["US", "IN", "CRYPTO"]


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WatchlistOut(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    created_at: datetime


class WatchlistListOut(BaseModel):
    watchlists: list[WatchlistOut]


class WatchlistItemIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    market: Market
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper()


class WatchlistItemOut(BaseModel):
    watchlist_id: UUID
    ticker: str
    market: Market
    added_at: datetime
    notes: str | None


class WatchlistItemListOut(BaseModel):
    items: list[WatchlistItemOut]
```

- [ ] **Step 4: SQL helpers**

Create `backend/app/db/watchlists.py`:

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.pool import acquire_conn

__all__ = [
    "acquire_conn", "list_watchlists", "create_watchlist", "delete_watchlist",
    "assert_watchlist_owned", "list_items", "insert_item", "delete_item",
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
            UUID(user_id), name,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_watchlist(user_id: str, wl_id: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM watchlists WHERE id = $1 AND user_id = $2",
            UUID(wl_id), UUID(user_id),
        )
    return status.endswith("1")


async def assert_watchlist_owned(user_id: str, wl_id: str) -> bool:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM watchlists WHERE id = $1 AND user_id = $2",
            UUID(wl_id), UUID(user_id),
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
    wl_id: str, ticker: str, market: str, notes: str | None,
) -> dict[str, Any]:
    async with acquire_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO watchlist_items (watchlist_id, ticker, market, notes) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (watchlist_id, ticker, market) DO UPDATE SET notes = EXCLUDED.notes "
            "RETURNING watchlist_id, ticker, market, added_at, notes",
            UUID(wl_id), ticker, market, notes,
        )
    if row is None:
        raise RuntimeError("INSERT returned no row")
    return dict(row)


async def delete_item(wl_id: str, ticker: str, market: str) -> bool:
    async with acquire_conn() as conn:
        status = await conn.execute(
            "DELETE FROM watchlist_items "
            "WHERE watchlist_id = $1 AND ticker = $2 AND market = $3",
            UUID(wl_id), ticker, market,
        )
    return status.endswith("1")
```

- [ ] **Step 5: Routes**

Create `backend/app/routes/watchlists.py`:

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth import get_current_user
from app.db import watchlists as db
from app.models.watchlist import (
    WatchlistIn, WatchlistItemIn, WatchlistItemListOut, WatchlistItemOut,
    WatchlistListOut, WatchlistOut,
)

router = APIRouter(tags=["watchlists"])


@router.get("/watchlists", response_model=WatchlistListOut)
async def list_endpoint(user: dict[str, Any] = Depends(get_current_user)) -> WatchlistListOut:
    rows = await db.list_watchlists(user["sub"])
    return WatchlistListOut(watchlists=[WatchlistOut(**r) for r in rows])


@router.post("/watchlists", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    req: WatchlistIn, user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistOut:
    row = await db.create_watchlist(user["sub"], req.name)
    return WatchlistOut(**row)


@router.delete("/watchlists/{wl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    wl_id: UUID, user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_watchlist(user["sub"], str(wl_id))
    if not ok:
        raise HTTPException(404, "Watchlist not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/watchlists/{wl_id}/items", response_model=WatchlistItemListOut)
async def list_items_endpoint(
    wl_id: UUID, user: dict[str, Any] = Depends(get_current_user),
) -> WatchlistItemListOut:
    if not await db.assert_watchlist_owned(user["sub"], str(wl_id)):
        raise HTTPException(404, "Watchlist not found")
    rows = await db.list_items(str(wl_id))
    return WatchlistItemListOut(items=[WatchlistItemOut(**r) for r in rows])


@router.post(
    "/watchlists/{wl_id}/items",
    response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED,
)
async def add_item_endpoint(
    wl_id: UUID, req: WatchlistItemIn,
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
    wl_id: UUID, ticker_market: str,
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
```

- [ ] **Step 6: Register router**

In `backend/app/main.py`:

```python
from app.routes import watchlists as watchlists_routes
app.include_router(watchlists_routes.router)
```

- [ ] **Step 7: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_watchlists.py -v
```

Expected: 5 passed.

- [ ] **Step 8: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 111 passing (106 prior + 5 new).

- [ ] **Step 9: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add -A && git commit -m "feat(backend): /watchlists + items CRUD

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: prices_cache module

**Files:**
- Create: `backend/app/db/prices.py`
- Create: `backend/app/data/prices_cache.py`
- Create: `backend/tests/test_prices_cache.py`

**Goal:** Pure cache-tier module. `get_prices(tickers)` reads `cache_kv`, fetches misses via yfinance in parallel, writes back with 15-min TTL. `invalidate_prices(tickers)` deletes keys. No HTTP routes yet — that's T6.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_prices_cache.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.yfinance_adapter import TickerInfo


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.mark.asyncio
async def test_get_prices_all_hits() -> None:
    """Cache has fresh entries for both tickers -> no yfinance call."""
    now = datetime.now()
    rows = [
        {"key": "price:AAPL:US",
         "value": {"price": 189.42, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
        {"key": "price:BTC:CRYPTO",
         "value": {"price": 80652.0, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
    ]
    fetch_mock = AsyncMock()
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_prices_all_misses_fetches_and_writes() -> None:
    fetch_mock = AsyncMock(side_effect=[
        TickerInfo(ticker="AAPL", name="Apple Inc.", currency="USD",
                   market_cap=None, last_price=189.42),
        TickerInfo(ticker="BTC", name="Bitcoin", currency="USD",
                   market_cap=None, last_price=80652.0),
    ])
    write_mock = AsyncMock()
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", write_mock):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    assert fetch_mock.await_count == 2
    write_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_prices_mixed_hits_and_misses() -> None:
    now = datetime.now()
    rows = [
        {"key": "price:AAPL:US",
         "value": {"price": 189.42, "currency": "USD", "as_of": now.isoformat()},
         "expires_at": now + timedelta(minutes=10)},
    ]
    fetch_mock = AsyncMock(return_value=TickerInfo(
        ticker="BTC", name="Bitcoin", currency="USD",
        market_cap=None, last_price=80652.0,
    ))
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert out[("AAPL", "US")].price == 189.42
    assert out[("BTC", "CRYPTO")].price == 80652.0
    assert fetch_mock.await_count == 1
    fetch_mock.assert_awaited_with("BTC", market="CRYPTO")


@pytest.mark.asyncio
async def test_get_prices_yfinance_error_returns_partial() -> None:
    """When yfinance fails for one ticker, others still come back."""
    fetch_mock = AsyncMock(side_effect=[
        TickerInfo(ticker="AAPL", name="Apple", currency="USD",
                   market_cap=None, last_price=189.42),
        RuntimeError("yfinance rate-limited"),
    ])
    with patch("app.data.prices_cache._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.prices_cache._fetch_ticker_info", fetch_mock), \
         patch("app.data.prices_cache._write_cache_rows", AsyncMock()):
        from app.data.prices_cache import get_prices
        out = await get_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    assert ("AAPL", "US") in out
    assert ("BTC", "CRYPTO") not in out


@pytest.mark.asyncio
async def test_invalidate_prices_deletes_keys() -> None:
    delete_mock = AsyncMock()
    with patch("app.data.prices_cache._delete_cache_keys", delete_mock):
        from app.data.prices_cache import invalidate_prices
        await invalidate_prices([("AAPL", "US"), ("BTC", "CRYPTO")])
    delete_mock.assert_awaited_once()
    keys = delete_mock.await_args.args[0]
    assert "price:AAPL:US" in keys
    assert "price:BTC:CRYPTO" in keys
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_prices_cache.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: DB helpers for cache_kv**

Create `backend/app/db/prices.py`:

```python
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
```

- [ ] **Step 4: prices_cache module**

Create `backend/app/data/prices_cache.py`:

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.data.yfinance_adapter import fetch_ticker_info as _fetch_ticker_info
from app.db.prices import (
    delete_cache_keys as _delete_cache_keys,
    fetch_cache_rows as _fetch_cache_rows,
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)
_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class Price:
    ticker: str
    market: str
    price: float
    currency: str
    as_of: datetime


def _key(ticker: str, market: str) -> str:
    return f"price:{ticker}:{market}"


async def get_prices(tickers: list[tuple[str, str]]) -> dict[tuple[str, str], Price]:
    """Return {(ticker, market): Price} for all hits + freshly-fetched misses.
    Tickers whose yfinance call fails are omitted from the response."""
    if not tickers:
        return {}
    keys = [_key(t, m) for t, m in tickers]
    rows = await _fetch_cache_rows(keys)
    hits: dict[str, dict[str, Any]] = {r["key"]: r["value"] for r in rows}
    out: dict[tuple[str, str], Price] = {}
    misses: list[tuple[str, str]] = []
    for ticker, market in tickers:
        if _key(ticker, market) in hits:
            v = hits[_key(ticker, market)]
            out[(ticker, market)] = Price(
                ticker=ticker, market=market,
                price=float(v["price"]), currency=v["currency"],
                as_of=datetime.fromisoformat(v["as_of"]),
            )
        else:
            misses.append((ticker, market))

    if not misses:
        return out

    fetched = await asyncio.gather(
        *[_fetch_ticker_info(t, market=m) for t, m in misses],
        return_exceptions=True,
    )
    now = datetime.now(timezone.utc)
    expires = now + _TTL
    write_rows: list[tuple[str, dict[str, Any], datetime]] = []
    for (ticker, market), result in zip(misses, fetched, strict=True):
        if isinstance(result, Exception):
            logger.warning("price fetch failed for %s:%s — %s", ticker, market, result)
            continue
        info = result
        if info.last_price is None:
            continue
        out[(ticker, market)] = Price(
            ticker=ticker, market=market,
            price=float(info.last_price), currency=info.currency,
            as_of=now,
        )
        write_rows.append((
            _key(ticker, market),
            {"price": float(info.last_price), "currency": info.currency, "as_of": now.isoformat()},
            expires,
        ))
    if write_rows:
        try:
            await _write_cache_rows(write_rows)
        except Exception as e:  # noqa: BLE001
            logger.warning("cache_kv write failed: %s (not fatal)", e)
    return out


async def invalidate_prices(tickers: list[tuple[str, str]]) -> None:
    keys = [_key(t, m) for t, m in tickers]
    await _delete_cache_keys(keys)
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_prices_cache.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 116 passing (111 prior + 5 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add -A && git commit -m "feat(backend): prices_cache module (cache_kv 15-min TTL, parallel yfinance miss-fetches)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: /positions GET with prices + cohorts, /prices batch, /prices/refresh

**Files:**
- Modify: `backend/app/db/portfolios.py` (add `list_positions`)
- Modify: `backend/app/models/portfolio.py` (add `PositionWithPrice`, `CohortSummary`, `PositionsResponse`)
- Modify: `backend/app/routes/portfolios.py` (add positions GET)
- Create: `backend/app/routes/prices.py`
- Create: `backend/tests/test_routes_prices.py`
- Create: `backend/tests/test_routes_e2e_portfolio.py`
- Modify: `backend/app/main.py` (register prices router)

**Goal:** The big one. The Holdings page calls `GET /portfolios/{id}/positions` once and gets back positions + live prices + per-cohort summaries. The watchlist page calls `GET /prices?tickers=...` for its rows. `POST /prices/refresh` invalidates so the next read goes to yfinance.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_routes_prices.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.data.prices_cache import Price
from tests.helpers.auth import bearer_for_test_user


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_batch_prices_returns_keyed_dict(client: TestClient) -> None:
    now = datetime(2026, 5, 15, 14, 23, 0)
    prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=189.42, currency="USD", as_of=now),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80652.0, currency="USD", as_of=now),
    }
    with patch("app.routes.prices.get_prices", AsyncMock(return_value=prices)):
        r = client.get(
            "/prices?tickers=AAPL:US,BTC:CRYPTO",
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prices"]["AAPL:US"]["price"] == 189.42
    assert body["prices"]["BTC:CRYPTO"]["price"] == 80652.0


def test_batch_prices_requires_auth(client: TestClient) -> None:
    assert client.get("/prices?tickers=AAPL:US").status_code == 401


def test_refresh_prices_invalidates_and_refetches(client: TestClient) -> None:
    invalidate_mock = AsyncMock()
    now = datetime(2026, 5, 15, 14, 30, 0)
    fresh = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=190.0, currency="USD", as_of=now),
    }
    with patch("app.routes.prices.invalidate_prices", invalidate_mock), \
         patch("app.routes.prices.get_prices", AsyncMock(return_value=fresh)):
        r = client.post(
            "/prices/refresh",
            json={"tickers": ["AAPL:US"]},
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 200
    invalidate_mock.assert_awaited_once_with([("AAPL", "US")])
    assert r.json()["prices"]["AAPL:US"]["price"] == 190.0


def test_batch_prices_rejects_bad_format(client: TestClient) -> None:
    r = client.get("/prices?tickers=NOSEP", headers=bearer_for_test_user())
    assert r.status_code == 422


def test_batch_prices_rejects_bad_market(client: TestClient) -> None:
    r = client.get("/prices?tickers=AAPL:EU", headers=bearer_for_test_user())
    assert r.status_code == 422
```

Create `backend/tests/test_routes_e2e_portfolio.py`:

```python
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.data.prices_cache import Price
from tests.helpers.auth import bearer_for_test_user, TEST_USER_ID


PORTFOLIO_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


def test_get_positions_groups_by_cohort(client: TestClient) -> None:
    conn = MagicMock()
    # ownership row + position rows
    conn.fetchrow = AsyncMock(return_value={"id": PORTFOLIO_ID})
    conn.fetch = AsyncMock(return_value=[
        {"id": "p1", "portfolio_id": PORTFOLIO_ID, "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 50, "cost_basis": 175.0, "currency": "USD",
         "opened_at": date(2024, 3, 15), "created_at": datetime(2026, 5, 15)},
        {"id": "p2", "portfolio_id": PORTFOLIO_ID, "ticker": "RELIANCE.NS", "market": "IN",
         "asset_class": "equity", "quantity": 100, "cost_basis": 1250.0, "currency": "INR",
         "opened_at": date(2024, 8, 2), "created_at": datetime(2026, 5, 15)},
        {"id": "p3", "portfolio_id": PORTFOLIO_ID, "ticker": "BTC", "market": "CRYPTO",
         "asset_class": "crypto", "quantity": 0.5, "cost_basis": 42000.0, "currency": "USD",
         "opened_at": date(2023, 11, 10), "created_at": datetime(2026, 5, 15)},
    ])
    now = datetime(2026, 5, 15, 14, 23, 0)
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=189.42, currency="USD", as_of=now),
        ("RELIANCE.NS", "IN"): Price(ticker="RELIANCE.NS", market="IN", price=1384.0, currency="INR", as_of=now),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80652.0, currency="USD", as_of=now),
    }
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.routes.portfolios.get_prices", AsyncMock(return_value=fake_prices)):
        r = client.get(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 3
    # cohorts are (currency, asset_class_group)
    by_currency = {c["currency"]: c for c in body["cohorts"]}
    assert "USD" in by_currency
    assert "INR" in by_currency
    # USD has 1 equity (AAPL) and 1 crypto (BTC) — they should be SEPARATE cohorts
    usd_cohorts = [c for c in body["cohorts"] if c["currency"] == "USD"]
    assert len(usd_cohorts) == 2
    eq = next(c for c in usd_cohorts if c["asset_class_group"] == "equity_etf")
    cy = next(c for c in usd_cohorts if c["asset_class_group"] == "crypto")
    assert eq["positions_count"] == 1
    assert cy["positions_count"] == 1
    # AAPL P/L: (189.42 - 175) / 175 * 100 ≈ 8.24
    aapl = next(p for p in body["positions"] if p["ticker"] == "AAPL")
    assert aapl["current_price"] == 189.42
    assert abs(aapl["pl_pct"] - 8.24) < 0.1


def test_get_positions_handles_missing_price(client: TestClient) -> None:
    """When yfinance fails for a ticker, response still returns the position with prices_partial=true."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": PORTFOLIO_ID})
    conn.fetch = AsyncMock(return_value=[
        {"id": "p1", "portfolio_id": PORTFOLIO_ID, "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 50, "cost_basis": 175.0, "currency": "USD",
         "opened_at": date(2024, 3, 15), "created_at": datetime(2026, 5, 15)},
    ])
    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.routes.portfolios.get_prices", AsyncMock(return_value={})):
        r = client.get(
            f"/portfolios/{PORTFOLIO_ID}/positions",
            headers=bearer_for_test_user(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prices_partial"] is True
    assert body["positions"][0]["current_price"] is None
```

- [ ] **Step 2: Confirm tests fail**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_prices.py tests/test_routes_e2e_portfolio.py -v
```

Expected: failures (`/prices` route missing; positions GET doesn't return cohorts).

- [ ] **Step 3: Extend models**

Append to `backend/app/models/portfolio.py`:

```python
class PositionWithPrice(PositionOut):
    current_price: float | None = None
    price_currency: str | None = None
    as_of: datetime | None = None
    pl_pct: float | None = None
    value_native: float | None = None


CohortGroup = Literal["equity_etf", "crypto"]


class CohortSummary(BaseModel):
    currency: str
    asset_class_group: CohortGroup
    positions_count: int
    total_cost_native: float
    total_value_native: float | None
    pl_pct: float | None
    as_of: datetime | None


class PositionsResponse(BaseModel):
    portfolio_id: UUID
    positions: list[PositionWithPrice]
    cohorts: list[CohortSummary]
    prices_partial: bool = False
```

- [ ] **Step 4: Add `list_positions` SQL helper**

Append to `backend/app/db/portfolios.py`:

```python
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
```

- [ ] **Step 5: Add positions GET to portfolios.py**

Append to `backend/app/routes/portfolios.py`:

```python
from collections import defaultdict
from app.data.prices_cache import get_prices
from app.models.portfolio import (
    CohortSummary, PositionWithPrice, PositionsResponse,
)


def _cohort_group(asset_class: str) -> str:
    return "crypto" if asset_class == "crypto" else "equity_etf"


@router.get("/portfolios/{portfolio_id}/positions", response_model=PositionsResponse)
async def list_positions_endpoint(
    portfolio_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionsResponse:
    if not await db.assert_portfolio_owned(user["sub"], str(portfolio_id)):
        raise HTTPException(404, "Portfolio not found")
    rows = await db.list_positions(user["sub"], str(portfolio_id))

    pairs = [(r["ticker"], r["market"]) for r in rows]
    prices = await get_prices(pairs)

    positions: list[PositionWithPrice] = []
    cohorts_acc: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"positions_count": 0, "total_cost_native": 0.0,
                 "total_value_native": 0.0, "as_of": None, "any_missing": False}
    )
    any_missing = False
    for r in rows:
        price = prices.get((r["ticker"], r["market"]))
        cost = float(r["cost_basis"]) if r["cost_basis"] is not None else 0.0
        qty = float(r["quantity"])
        cost_native = cost * qty
        if price is not None:
            value_native = price.price * qty
            pl_pct = ((price.price - cost) / cost * 100) if cost > 0 else None
        else:
            any_missing = True
            value_native = None
            pl_pct = None

        positions.append(PositionWithPrice(
            **r,
            current_price=price.price if price else None,
            price_currency=price.currency if price else None,
            as_of=price.as_of if price else None,
            pl_pct=pl_pct, value_native=value_native,
        ))

        key = (r["currency"], _cohort_group(r["asset_class"]))
        c = cohorts_acc[key]
        c["positions_count"] += 1
        c["total_cost_native"] += cost_native
        if value_native is not None:
            if c["total_value_native"] is None:
                c["total_value_native"] = 0.0
            c["total_value_native"] += value_native
            c["as_of"] = price.as_of if c["as_of"] is None else max(c["as_of"], price.as_of)
        else:
            c["any_missing"] = True

    cohorts_out: list[CohortSummary] = []
    for (currency, group), acc in cohorts_acc.items():
        tv = acc["total_value_native"] if not acc["any_missing"] else None
        cost_t = acc["total_cost_native"]
        pl = ((tv - cost_t) / cost_t * 100) if (tv is not None and cost_t > 0) else None
        cohorts_out.append(CohortSummary(
            currency=currency, asset_class_group=group,  # type: ignore[arg-type]
            positions_count=acc["positions_count"],
            total_cost_native=cost_t, total_value_native=tv,
            pl_pct=pl, as_of=acc["as_of"],
        ))

    return PositionsResponse(
        portfolio_id=portfolio_id,
        positions=positions, cohorts=cohorts_out,
        prices_partial=any_missing,
    )
```

- [ ] **Step 6: Create the /prices router**

Create `backend/app/routes/prices.py`:

```python
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
        "price": p.price, "currency": p.currency, "as_of": p.as_of.isoformat(),
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
    req: RefreshRequest, user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    pairs = _parse_tickers(",".join(req.tickers))
    await invalidate_prices(pairs)
    prices = await get_prices(pairs)
    out = {f"{t}:{m}": _price_json(p) for (t, m), p in prices.items()}
    return {"prices": out, "prices_partial": len(out) != len(pairs)}
```

- [ ] **Step 7: Register the prices router**

In `backend/app/main.py`:

```python
from app.routes import prices as prices_routes
app.include_router(prices_routes.router)
```

- [ ] **Step 8: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_prices.py tests/test_routes_e2e_portfolio.py -v
```

Expected: 7 passed (5 + 2).

- [ ] **Step 9: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 123 passing (116 prior + 7 new).

- [ ] **Step 10: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add -A && git commit -m "feat(backend): positions GET (cohort-grouped + live prices) + /prices batch + /prices/refresh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend API clients + `/portfolio` page scaffold + nav

**Files:**
- Create: `frontend/lib/api-base.ts`
- Create: `frontend/lib/api-resolve.ts`
- Create: `frontend/lib/api-portfolios.ts`
- Create: `frontend/lib/api-watchlists.ts`
- Create: `frontend/lib/api-prices.ts`
- Create: `frontend/app/portfolio/layout.tsx`
- Create: `frontend/app/portfolio/page.tsx`
- Modify: `frontend/components/sidebar.tsx` (add Portfolio link)

**Goal:** Skeleton frontend route under `/portfolio` with two tabs, plus typed fetch wrappers for every backend endpoint added in T1-T6. Tabs render placeholders (`<PortfolioTab />` / `<WatchlistTab />` from T8/T10).

**Reminder:** before editing, run `cat frontend/AGENTS.md` and skim `frontend/node_modules/next/dist/docs/` for current app-router conventions — the project warns its Next version differs from training data.

- [ ] **Step 1: Shared fetch wrapper**

Create `frontend/lib/api-base.ts`:

```ts
import { createBrowserClient } from "@supabase/ssr";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

let supabase: ReturnType<typeof createBrowserClient> | null = null;
function getSupabase() {
  if (!supabase) {
    supabase = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    );
  }
  return supabase;
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { data: { session } } = await getSupabase().auth.getSession();
  if (!session) throw new Error("Not authenticated");

  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
      ...init.headers,
    },
  });

  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body?.detail ?? body?.message ?? res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}
```

If the project already has a different Supabase client pattern (e.g., a server-side `lib/supabase.ts`), match that import. The `createBrowserClient` from `@supabase/ssr` is the standard browser-side path.

- [ ] **Step 2: Typed API wrappers**

Create `frontend/lib/api-resolve.ts`:

```ts
import { apiFetch } from "./api-base";

export type TickerResolution = {
  ticker: string;
  name: string;
  market: "US" | "IN" | "CRYPTO";
  asset_class: "equity" | "etf" | "crypto";
  confidence: number;
};

export async function resolveTicker(query: string): Promise<TickerResolution> {
  return apiFetch<TickerResolution>("/resolve", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}
```

Create `frontend/lib/api-portfolios.ts`:

```ts
import { apiFetch } from "./api-base";

export type Market = "US" | "IN" | "CRYPTO";
export type AssetClass = "equity" | "etf" | "crypto";
export type CohortGroup = "equity_etf" | "crypto";

export type Portfolio = {
  id: string;
  user_id: string;
  name: string;
  base_currency: string;
  created_at: string;
  updated_at: string;
};

export type Position = {
  id: string;
  portfolio_id: string;
  ticker: string;
  market: Market;
  asset_class: AssetClass;
  quantity: number | string;
  cost_basis: number | string | null;
  currency: string;
  opened_at: string | null;
  created_at: string;
  current_price: number | null;
  price_currency: string | null;
  as_of: string | null;
  pl_pct: number | null;
  value_native: number | null;
};

export type CohortSummary = {
  currency: string;
  asset_class_group: CohortGroup;
  positions_count: number;
  total_cost_native: number;
  total_value_native: number | null;
  pl_pct: number | null;
  as_of: string | null;
};

export type PositionsResponse = {
  portfolio_id: string;
  positions: Position[];
  cohorts: CohortSummary[];
  prices_partial: boolean;
};

export async function listPortfolios(): Promise<Portfolio[]> {
  const r = await apiFetch<{ portfolios: Portfolio[] }>("/portfolios");
  return r.portfolios;
}

export async function createPortfolio(name: string, base_currency: string): Promise<Portfolio> {
  return apiFetch<Portfolio>("/portfolios", {
    method: "POST",
    body: JSON.stringify({ name, base_currency }),
  });
}

export async function deletePortfolio(id: string): Promise<void> {
  await apiFetch(`/portfolios/${id}`, { method: "DELETE" });
}

export async function listPositions(portfolioId: string): Promise<PositionsResponse> {
  return apiFetch<PositionsResponse>(`/portfolios/${portfolioId}/positions`);
}

export type PositionInput = {
  ticker: string;
  market: Market;
  asset_class: AssetClass;
  quantity: number;
  cost_basis: number;
  currency: string;
  opened_at?: string;
};

export async function addPosition(portfolioId: string, p: PositionInput): Promise<Position> {
  return apiFetch<Position>(`/portfolios/${portfolioId}/positions`, {
    method: "POST",
    body: JSON.stringify(p),
  });
}

export type PositionPatch = {
  quantity?: number;
  cost_basis?: number;
  opened_at?: string;
};

export async function patchPosition(positionId: string, patch: PositionPatch): Promise<Position> {
  return apiFetch<Position>(`/positions/${positionId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deletePosition(positionId: string): Promise<void> {
  await apiFetch(`/positions/${positionId}`, { method: "DELETE" });
}
```

Create `frontend/lib/api-watchlists.ts`:

```ts
import { apiFetch } from "./api-base";
import type { Market } from "./api-portfolios";

export type Watchlist = {
  id: string;
  user_id: string;
  name: string;
  created_at: string;
};

export type WatchlistItem = {
  watchlist_id: string;
  ticker: string;
  market: Market;
  added_at: string;
  notes: string | null;
};

export async function listWatchlists(): Promise<Watchlist[]> {
  const r = await apiFetch<{ watchlists: Watchlist[] }>("/watchlists");
  return r.watchlists;
}

export async function createWatchlist(name: string): Promise<Watchlist> {
  return apiFetch<Watchlist>("/watchlists", {
    method: "POST", body: JSON.stringify({ name }),
  });
}

export async function deleteWatchlist(id: string): Promise<void> {
  await apiFetch(`/watchlists/${id}`, { method: "DELETE" });
}

export async function listWatchlistItems(wlId: string): Promise<WatchlistItem[]> {
  const r = await apiFetch<{ items: WatchlistItem[] }>(`/watchlists/${wlId}/items`);
  return r.items;
}

export async function addWatchlistItem(
  wlId: string, ticker: string, market: Market, notes?: string,
): Promise<WatchlistItem> {
  return apiFetch<WatchlistItem>(`/watchlists/${wlId}/items`, {
    method: "POST", body: JSON.stringify({ ticker, market, notes }),
  });
}

export async function deleteWatchlistItem(
  wlId: string, ticker: string, market: Market,
): Promise<void> {
  await apiFetch(`/watchlists/${wlId}/items/${ticker}:${market}`, { method: "DELETE" });
}
```

Create `frontend/lib/api-prices.ts`:

```ts
import { apiFetch } from "./api-base";
import type { Market } from "./api-portfolios";

export type PriceQuote = {
  price: number;
  currency: string;
  as_of: string;
};

export type PricesResponse = {
  prices: Record<string, PriceQuote>;
  prices_partial: boolean;
};

export function tickerMarketKey(ticker: string, market: Market): string {
  return `${ticker}:${market}`;
}

export async function batchPrices(pairs: Array<[string, Market]>): Promise<PricesResponse> {
  const q = pairs.map(([t, m]) => `${t}:${m}`).join(",");
  return apiFetch<PricesResponse>(`/prices?tickers=${encodeURIComponent(q)}`);
}

export async function refreshPrices(pairs: Array<[string, Market]>): Promise<PricesResponse> {
  return apiFetch<PricesResponse>("/prices/refresh", {
    method: "POST",
    body: JSON.stringify({ tickers: pairs.map(([t, m]) => `${t}:${m}`) }),
  });
}
```

- [ ] **Step 3: Create the portfolio layout (auth gate)**

Create `frontend/app/portfolio/layout.tsx`:

```tsx
import { redirect } from "next/navigation";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export default async function PortfolioLayout({
  children,
}: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return cookieStore.getAll(); },
        setAll() {},
      },
    },
  );
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) redirect("/login");
  return <div className="container mx-auto py-6">{children}</div>;
}
```

If `/chat`'s existing layout uses a different auth pattern (e.g., middleware redirect), mirror that exactly instead.

- [ ] **Step 4: Page scaffold with tabs**

Create `frontend/app/portfolio/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { PortfolioTab } from "@/components/portfolio-tab";
import { WatchlistTab } from "@/components/watchlist-tab";

type Tab = "holdings" | "watchlist";

export default function PortfolioPage() {
  const [tab, setTab] = useState<Tab>("holdings");
  return (
    <div className="space-y-4">
      <div className="flex border-b">
        <button
          onClick={() => setTab("holdings")}
          className={`px-4 py-2 ${tab === "holdings" ? "border-b-2 border-blue-500 font-semibold" : "text-gray-500"}`}
        >
          Holdings
        </button>
        <button
          onClick={() => setTab("watchlist")}
          className={`px-4 py-2 ${tab === "watchlist" ? "border-b-2 border-blue-500 font-semibold" : "text-gray-500"}`}
        >
          Watchlist
        </button>
      </div>
      {tab === "holdings" ? <PortfolioTab /> : <WatchlistTab />}
    </div>
  );
}
```

For now, create placeholder stub components so the page compiles:

Create `frontend/components/portfolio-tab.tsx`:

```tsx
"use client";
export function PortfolioTab() {
  return <div>Holdings tab (T8 implements this)</div>;
}
```

Create `frontend/components/watchlist-tab.tsx`:

```tsx
"use client";
export function WatchlistTab() {
  return <div>Watchlist tab (T10 implements this)</div>;
}
```

- [ ] **Step 5: Sidebar nav**

Find the existing sidebar/topnav component (likely `frontend/components/sidebar.tsx` or `frontend/components/topnav.tsx`). Add a `/portfolio` link next to the `/chat` link:

```tsx
<Link href="/portfolio" className="...">Portfolio</Link>
```

Match whatever styling and icon pattern the existing chat link uses.

- [ ] **Step 6: Smoke check the build**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run build
```

Expected: build succeeds. If TypeScript errors appear, fix them inline before continuing.

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "feat(frontend): /portfolio scaffold + API clients + sidebar link

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Holdings tab — selector, cohort cards, grouped table

**Files:**
- Create: `frontend/components/portfolio-selector.tsx`
- Create: `frontend/components/cohort-card.tsx`
- Create: `frontend/components/portfolio-table.tsx`
- Create: `frontend/components/refresh-button.tsx`
- Modify: `frontend/components/portfolio-tab.tsx` (real implementation)

**Goal:** The Holdings tab now lists positions grouped by cohort, shows cohort summary cards, supports portfolio switching and price refresh. Edit/delete row actions wire to T9 modals later — for now expose `[Edit]` `[Delete]` buttons.

- [ ] **Step 1: Portfolio selector**

Create `frontend/components/portfolio-selector.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { Portfolio } from "@/lib/api-portfolios";
import { createPortfolio } from "@/lib/api-portfolios";

export function PortfolioSelector({
  portfolios, selectedId, onSelect, onCreate,
}: {
  portfolios: Portfolio[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (p: Portfolio) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCurrency, setNewCurrency] = useState("USD");
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!newName.trim()) return setError("Name required");
    try {
      const p = await createPortfolio(newName.trim(), newCurrency.toUpperCase());
      onCreate(p);
      setCreating(false);
      setNewName("");
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create");
    }
  }

  return (
    <div className="flex items-center gap-2">
      <select
        className="border rounded px-2 py-1"
        value={selectedId ?? ""}
        onChange={(e) => onSelect(e.target.value)}
      >
        {portfolios.map((p) => (
          <option key={p.id} value={p.id}>{p.name} ({p.base_currency})</option>
        ))}
      </select>
      <button onClick={() => setCreating(true)} className="border rounded px-3 py-1">
        + New
      </button>

      {creating && (
        <div className="ml-2 flex items-center gap-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Portfolio name"
            className="border rounded px-2 py-1"
          />
          <input
            value={newCurrency}
            onChange={(e) => setNewCurrency(e.target.value)}
            placeholder="USD"
            maxLength={3}
            className="border rounded px-2 py-1 w-16"
          />
          <button onClick={handleCreate} className="border rounded px-3 py-1 bg-blue-500 text-white">
            Create
          </button>
          <button onClick={() => { setCreating(false); setError(null); }} className="text-gray-500">
            Cancel
          </button>
          {error && <span className="text-red-500 text-sm">{error}</span>}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Cohort card**

Create `frontend/components/cohort-card.tsx`:

```tsx
"use client";

import type { CohortSummary } from "@/lib/api-portfolios";

const currencySymbol: Record<string, string> = {
  USD: "$", INR: "₹", EUR: "€", GBP: "£",
};

function fmt(currency: string, value: number | null): string {
  if (value === null) return "—";
  const sym = currencySymbol[currency] ?? `${currency} `;
  return `${sym}${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function pct(p: number | null): string {
  if (p === null) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${p.toFixed(1)}%`;
}

export function CohortCard({ c }: { c: CohortSummary }) {
  const label =
    c.asset_class_group === "crypto"
      ? `${c.currency} (Crypto)`
      : `${c.currency} (Equities & ETFs)`;
  const pctClass = c.pl_pct === null
    ? "text-gray-400"
    : c.pl_pct >= 0 ? "text-green-600" : "text-red-600";
  return (
    <div className="border rounded p-3 min-w-[140px]">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-lg font-semibold">{fmt(c.currency, c.total_value_native)}</div>
      <div className={`text-sm ${pctClass}`}>{pct(c.pl_pct)}</div>
      <div className="text-xs text-gray-400">
        {c.positions_count} {c.positions_count === 1 ? "position" : "positions"}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Portfolio table (grouped)**

Create `frontend/components/portfolio-table.tsx`:

```tsx
"use client";

import type { Position, CohortSummary } from "@/lib/api-portfolios";

const currencySymbol: Record<string, string> = {
  USD: "$", INR: "₹", EUR: "€", GBP: "£",
};

function fmt(currency: string | null, value: number | string | null): string {
  if (value === null || value === undefined) return "—";
  const num = typeof value === "string" ? parseFloat(value) : value;
  const sym = currency ? (currencySymbol[currency] ?? `${currency} `) : "";
  return `${sym}${num.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function pct(p: number | null): string {
  if (p === null) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${p.toFixed(1)}%`;
}

function cohortGroup(asset_class: string): "equity_etf" | "crypto" {
  return asset_class === "crypto" ? "crypto" : "equity_etf";
}

export function PortfolioTable({
  positions, cohorts, onEdit, onDelete,
}: {
  positions: Position[];
  cohorts: CohortSummary[];
  onEdit: (p: Position) => void;
  onDelete: (p: Position) => void;
}) {
  if (positions.length === 0) {
    return (
      <div className="border-dashed border rounded p-8 text-center text-gray-500">
        No positions yet. Click [+ Add position] to start.
      </div>
    );
  }

  // Bucket positions by cohort key (currency, asset_class_group)
  const buckets = new Map<string, Position[]>();
  for (const p of positions) {
    const key = `${p.currency}:${cohortGroup(p.asset_class)}`;
    buckets.set(key, [...(buckets.get(key) ?? []), p]);
  }

  return (
    <div className="border rounded">
      {cohorts.map((c) => {
        const key = `${c.currency}:${c.asset_class_group}`;
        const rows = buckets.get(key) ?? [];
        const label =
          c.asset_class_group === "crypto"
            ? `${c.currency} (Crypto)`
            : `${c.currency} (Equities & ETFs)`;
        return (
          <div key={key}>
            <div className="bg-gray-50 px-3 py-2 text-sm font-medium flex justify-between">
              <span>── {label} — {c.positions_count} positions ──</span>
              <span>
                {fmt(c.currency, c.total_value_native)} {" "}
                <span className={c.pl_pct === null ? "text-gray-400" : c.pl_pct >= 0 ? "text-green-600" : "text-red-600"}>
                  {pct(c.pl_pct)}
                </span>
              </span>
            </div>
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500">
                <tr>
                  <th className="text-left px-3 py-1">Ticker</th>
                  <th className="text-right px-3 py-1">Qty</th>
                  <th className="text-right px-3 py-1">Cost</th>
                  <th className="text-right px-3 py-1">Now</th>
                  <th className="text-right px-3 py-1">P/L</th>
                  <th className="px-3 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="border-t">
                    <td className="px-3 py-2 font-mono">{p.ticker}</td>
                    <td className="px-3 py-2 text-right">{p.quantity}</td>
                    <td className="px-3 py-2 text-right">{fmt(p.currency, p.cost_basis)}</td>
                    <td className="px-3 py-2 text-right">{fmt(p.currency, p.current_price)}</td>
                    <td className={`px-3 py-2 text-right ${p.pl_pct === null ? "text-gray-400" : p.pl_pct >= 0 ? "text-green-600" : "text-red-600"}`}>
                      {pct(p.pl_pct)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => onEdit(p)} className="text-blue-500 mr-2">✎</button>
                      <button onClick={() => onDelete(p)} className="text-red-500">🗑</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Refresh button**

Create `frontend/components/refresh-button.tsx`:

```tsx
"use client";

import { useState } from "react";

export function RefreshButton({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const [loading, setLoading] = useState(false);
  return (
    <button
      onClick={async () => {
        setLoading(true);
        try { await onRefresh(); } finally { setLoading(false); }
      }}
      disabled={loading}
      className="border rounded px-3 py-1 hover:bg-gray-50 disabled:opacity-50"
    >
      {loading ? "Refreshing..." : "⟳ Refresh prices"}
    </button>
  );
}
```

- [ ] **Step 5: Real Holdings tab**

Replace `frontend/components/portfolio-tab.tsx`:

```tsx
"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listPortfolios, listPositions, deletePosition,
  type Portfolio, type Position, type PositionsResponse,
} from "@/lib/api-portfolios";
import { refreshPrices } from "@/lib/api-prices";
import { PortfolioSelector } from "./portfolio-selector";
import { CohortCard } from "./cohort-card";
import { PortfolioTable } from "./portfolio-table";
import { RefreshButton } from "./refresh-button";

export function PortfolioTab() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [data, setData] = useState<PositionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPortfolios = useCallback(async () => {
    try {
      const pf = await listPortfolios();
      setPortfolios(pf);
      if (pf.length > 0 && !selectedId) setSelectedId(pf[0].id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selectedId]);

  const loadPositions = useCallback(async () => {
    if (!selectedId) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      setData(await listPositions(selectedId));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { void loadPortfolios(); }, [loadPortfolios]);
  useEffect(() => { void loadPositions(); }, [loadPositions]);

  async function handleRefresh() {
    if (!data) return;
    const pairs = data.positions.map(p => [p.ticker, p.market] as [string, "US" | "IN" | "CRYPTO"]);
    if (pairs.length === 0) return;
    await refreshPrices(pairs);
    await loadPositions();
  }

  async function handleDelete(p: Position) {
    if (!confirm(`Delete ${p.ticker}?`)) return;
    await deletePosition(p.id);
    await loadPositions();
  }

  if (portfolios.length === 0 && !loading) {
    return (
      <div className="border-dashed border rounded p-8 text-center">
        <p className="text-gray-500 mb-3">Welcome. No portfolios yet.</p>
        <PortfolioSelector
          portfolios={portfolios} selectedId={null}
          onSelect={setSelectedId}
          onCreate={(p) => { setPortfolios([p]); setSelectedId(p.id); }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <PortfolioSelector
          portfolios={portfolios}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreate={(p) => { setPortfolios([...portfolios, p]); setSelectedId(p.id); }}
        />
        <div className="flex gap-2">
          <RefreshButton onRefresh={handleRefresh} />
          <button className="border rounded px-3 py-1 bg-blue-500 text-white">
            + Add position
          </button>
          {/* The Add modal is wired in T9 */}
        </div>
      </div>

      {error && <div className="bg-red-50 text-red-700 px-3 py-2 rounded">{error}</div>}

      {data && data.cohorts.length > 0 && (
        <div className="flex gap-3 flex-wrap">
          {data.cohorts.map((c) => (
            <CohortCard key={`${c.currency}:${c.asset_class_group}`} c={c} />
          ))}
        </div>
      )}

      {data && (
        <PortfolioTable
          positions={data.positions}
          cohorts={data.cohorts}
          onEdit={() => {}}
          onDelete={handleDelete}
        />
      )}

      {data?.prices_partial && (
        <div className="bg-yellow-50 text-yellow-800 px-3 py-2 rounded">
          Prices temporarily unavailable for some tickers. Click ⟳ to retry.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Build + smoke test**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run build
```

Expected: success.

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "feat(frontend): Holdings tab — selector, cohort cards, grouped table, refresh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Add/edit position modals + ticker autocomplete

**Files:**
- Create: `frontend/components/ticker-autocomplete.tsx`
- Create: `frontend/components/add-position-modal.tsx`
- Create: `frontend/components/edit-position-modal.tsx`
- Modify: `frontend/components/portfolio-tab.tsx` (wire modals in)

**Goal:** The "+ Add position" button opens a modal with a ticker autocomplete that calls `/resolve`; once a ticker is resolved it auto-fills market and asset_class; user enters qty, cost, currency, opened_at and submits. Edit modal is similar but pre-filled and locks the ticker.

- [ ] **Step 1: Ticker autocomplete**

Create `frontend/components/ticker-autocomplete.tsx`:

```tsx
"use client";

import { useState } from "react";
import { resolveTicker, type TickerResolution } from "@/lib/api-resolve";

export function TickerAutocomplete({
  onPick, autoFocus,
}: {
  onPick: (r: TickerResolution) => void;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<TickerResolution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleResolve() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const r = await resolveTicker(query.trim());
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          autoFocus={autoFocus}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleResolve()}
          placeholder="Apple / RELIANCE.NS / BTC"
          className="border rounded px-2 py-1 flex-1"
        />
        <button
          onClick={handleResolve}
          disabled={loading || !query.trim()}
          className="border rounded px-3 py-1"
        >
          {loading ? "..." : "Resolve"}
        </button>
      </div>
      {error && <div className="text-red-500 text-sm">{error}</div>}
      {result && (
        <div className="border rounded p-2 bg-blue-50">
          <div className="font-mono font-semibold">{result.ticker}</div>
          <div className="text-sm">{result.name} • {result.market} • {result.asset_class}</div>
          <div className="text-xs text-gray-500">confidence: {result.confidence.toFixed(2)}</div>
          <button
            onClick={() => onPick(result)}
            className="mt-1 border rounded px-3 py-1 bg-blue-500 text-white text-sm"
          >
            Use this ticker
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add position modal**

Create `frontend/components/add-position-modal.tsx`:

```tsx
"use client";

import { useState } from "react";
import { addPosition, type Position, type Market, type AssetClass } from "@/lib/api-portfolios";
import { TickerAutocomplete } from "./ticker-autocomplete";

const CURRENCY_BY_MARKET: Record<Market, string> = {
  US: "USD", IN: "INR", CRYPTO: "USD",
};

export function AddPositionModal({
  portfolioId, onClose, onSaved,
}: {
  portfolioId: string;
  onClose: () => void;
  onSaved: (p: Position) => void;
}) {
  const [step, setStep] = useState<"pick" | "fill">("pick");
  const [resolved, setResolved] = useState<{
    ticker: string; market: Market; asset_class: AssetClass;
  } | null>(null);
  const [quantity, setQuantity] = useState("");
  const [costBasis, setCostBasis] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [openedAt, setOpenedAt] = useState<string>(
    new Date().toISOString().slice(0, 10),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    if (!resolved) return;
    const qty = parseFloat(quantity);
    const cost = parseFloat(costBasis);
    if (!Number.isFinite(qty) || qty <= 0) return setError("Quantity must be > 0");
    if (!Number.isFinite(cost) || cost < 0) return setError("Cost basis must be ≥ 0");
    setSubmitting(true);
    try {
      const p = await addPosition(portfolioId, {
        ticker: resolved.ticker, market: resolved.market, asset_class: resolved.asset_class,
        quantity: qty, cost_basis: cost, currency: currency.toUpperCase(),
        opened_at: openedAt,
      });
      onSaved(p);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded p-6 w-full max-w-md space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">Add position</h2>
          <button onClick={onClose} className="text-gray-500">✕</button>
        </div>

        {step === "pick" && (
          <TickerAutocomplete
            autoFocus
            onPick={(r) => {
              setResolved({ ticker: r.ticker, market: r.market, asset_class: r.asset_class });
              setCurrency(CURRENCY_BY_MARKET[r.market]);
              setStep("fill");
            }}
          />
        )}

        {step === "fill" && resolved && (
          <>
            <div className="bg-gray-50 rounded p-2 text-sm">
              <span className="font-mono font-semibold">{resolved.ticker}</span> • {resolved.market} • {resolved.asset_class}
              <button
                onClick={() => { setResolved(null); setStep("pick"); }}
                className="text-xs text-blue-500 ml-2"
              >
                change
              </button>
            </div>

            <div>
              <label className="block text-sm">Quantity</label>
              <input
                type="number" step="any" min="0"
                value={quantity} onChange={(e) => setQuantity(e.target.value)}
                className="border rounded px-2 py-1 w-full"
              />
            </div>
            <div>
              <label className="block text-sm">Cost basis (per unit, {currency})</label>
              <input
                type="number" step="any" min="0"
                value={costBasis} onChange={(e) => setCostBasis(e.target.value)}
                className="border rounded px-2 py-1 w-full"
              />
            </div>
            <details>
              <summary className="text-sm cursor-pointer">Override currency</summary>
              <input
                value={currency} onChange={(e) => setCurrency(e.target.value)}
                maxLength={3}
                className="border rounded px-2 py-1 w-24 mt-1 uppercase"
              />
            </details>
            <div>
              <label className="block text-sm">Opened on</label>
              <input
                type="date"
                value={openedAt} onChange={(e) => setOpenedAt(e.target.value)}
                className="border rounded px-2 py-1"
              />
            </div>

            {error && <div className="text-red-500 text-sm">{error}</div>}

            <div className="flex justify-end gap-2">
              <button onClick={onClose} className="border rounded px-3 py-1">Cancel</button>
              <button
                onClick={handleSave}
                disabled={submitting}
                className="border rounded px-3 py-1 bg-blue-500 text-white disabled:opacity-50"
              >
                {submitting ? "Saving..." : "Save position"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Edit position modal**

Create `frontend/components/edit-position-modal.tsx`:

```tsx
"use client";

import { useState } from "react";
import { patchPosition, type Position } from "@/lib/api-portfolios";

export function EditPositionModal({
  position, onClose, onSaved,
}: {
  position: Position;
  onClose: () => void;
  onSaved: (p: Position) => void;
}) {
  const [quantity, setQuantity] = useState(String(position.quantity));
  const [costBasis, setCostBasis] = useState(
    position.cost_basis === null ? "" : String(position.cost_basis),
  );
  const [openedAt, setOpenedAt] = useState(position.opened_at ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    const qty = parseFloat(quantity);
    const cost = parseFloat(costBasis);
    if (!Number.isFinite(qty) || qty <= 0) return setError("Quantity must be > 0");
    if (!Number.isFinite(cost) || cost < 0) return setError("Cost basis must be ≥ 0");
    setSubmitting(true);
    try {
      const p = await patchPosition(position.id, {
        quantity: qty, cost_basis: cost,
        opened_at: openedAt || undefined,
      });
      onSaved(p);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded p-6 w-full max-w-md space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">Edit {position.ticker}</h2>
          <button onClick={onClose} className="text-gray-500">✕</button>
        </div>
        <div>
          <label className="block text-sm">Quantity</label>
          <input
            type="number" step="any" min="0"
            value={quantity} onChange={(e) => setQuantity(e.target.value)}
            className="border rounded px-2 py-1 w-full"
          />
        </div>
        <div>
          <label className="block text-sm">Cost basis (per unit, {position.currency})</label>
          <input
            type="number" step="any" min="0"
            value={costBasis} onChange={(e) => setCostBasis(e.target.value)}
            className="border rounded px-2 py-1 w-full"
          />
        </div>
        <div>
          <label className="block text-sm">Opened on</label>
          <input
            type="date"
            value={openedAt} onChange={(e) => setOpenedAt(e.target.value)}
            className="border rounded px-2 py-1"
          />
        </div>
        {error && <div className="text-red-500 text-sm">{error}</div>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="border rounded px-3 py-1">Cancel</button>
          <button
            onClick={handleSave}
            disabled={submitting}
            className="border rounded px-3 py-1 bg-blue-500 text-white disabled:opacity-50"
          >
            {submitting ? "Saving..." : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire modals into the Holdings tab**

In `frontend/components/portfolio-tab.tsx`:

1. Add imports:

```tsx
import { AddPositionModal } from "./add-position-modal";
import { EditPositionModal } from "./edit-position-modal";
```

2. Add state for which modal is open:

```tsx
const [addOpen, setAddOpen] = useState(false);
const [editing, setEditing] = useState<Position | null>(null);
```

3. Replace the dummy `+ Add position` button's onClick:

```tsx
<button
  onClick={() => setAddOpen(true)}
  className="border rounded px-3 py-1 bg-blue-500 text-white"
>
  + Add position
</button>
```

4. Replace `onEdit={() => {}}` on `<PortfolioTable>` with `onEdit={setEditing}`.

5. Render the modals at the bottom of the component:

```tsx
{addOpen && selectedId && (
  <AddPositionModal
    portfolioId={selectedId}
    onClose={() => setAddOpen(false)}
    onSaved={() => loadPositions()}
  />
)}
{editing && (
  <EditPositionModal
    position={editing}
    onClose={() => setEditing(null)}
    onSaved={() => loadPositions()}
  />
)}
```

- [ ] **Step 5: Build smoke test**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run build
```

Expected: success.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "feat(frontend): add/edit position modals + ticker autocomplete

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Watchlist tab

**Files:**
- Create: `frontend/components/watchlist-selector.tsx`
- Create: `frontend/components/watchlist-table.tsx`
- Modify: `frontend/components/watchlist-tab.tsx` (real implementation)

**Goal:** Watchlist tab functional end-to-end: selector + create, add item via ticker autocomplete (reuses T9 component), table with live prices via batch /prices endpoint, delete row.

- [ ] **Step 1: Watchlist selector**

Create `frontend/components/watchlist-selector.tsx`:

```tsx
"use client";

import { useState } from "react";
import { createWatchlist, type Watchlist } from "@/lib/api-watchlists";

export function WatchlistSelector({
  watchlists, selectedId, onSelect, onCreate,
}: {
  watchlists: Watchlist[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (w: Watchlist) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!name.trim()) return setError("Name required");
    try {
      const w = await createWatchlist(name.trim());
      onCreate(w);
      setCreating(false);
      setName("");
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex items-center gap-2">
      <select
        className="border rounded px-2 py-1"
        value={selectedId ?? ""}
        onChange={(e) => onSelect(e.target.value)}
      >
        {watchlists.map((w) => (
          <option key={w.id} value={w.id}>{w.name}</option>
        ))}
      </select>
      <button onClick={() => setCreating(true)} className="border rounded px-3 py-1">
        + New
      </button>
      {creating && (
        <div className="flex items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Watchlist name"
            className="border rounded px-2 py-1"
          />
          <button onClick={handleCreate} className="border rounded px-3 py-1 bg-blue-500 text-white">
            Create
          </button>
          <button onClick={() => { setCreating(false); setError(null); }} className="text-gray-500">
            Cancel
          </button>
          {error && <span className="text-red-500 text-sm">{error}</span>}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Watchlist table**

Create `frontend/components/watchlist-table.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { WatchlistItem } from "@/lib/api-watchlists";
import type { PriceQuote } from "@/lib/api-prices";
import { deleteWatchlistItem } from "@/lib/api-watchlists";

const currencySymbol: Record<string, string> = {
  USD: "$", INR: "₹", EUR: "€", GBP: "£",
};

function fmt(currency: string | undefined, value: number | undefined): string {
  if (value === undefined) return "—";
  const sym = currency ? (currencySymbol[currency] ?? `${currency} `) : "";
  return `${sym}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function WatchlistTable({
  items, prices, watchlistId, onDeleted,
}: {
  items: WatchlistItem[];
  prices: Record<string, PriceQuote>;
  watchlistId: string;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  async function handleDelete(it: WatchlistItem) {
    if (!confirm(`Remove ${it.ticker}?`)) return;
    setBusy(`${it.ticker}:${it.market}`);
    try {
      await deleteWatchlistItem(watchlistId, it.ticker, it.market);
      onDeleted();
    } finally {
      setBusy(null);
    }
  }

  if (items.length === 0) {
    return (
      <div className="border-dashed border rounded p-8 text-center text-gray-500">
        No tickers yet. Click [+ Add ticker] to start.
      </div>
    );
  }

  return (
    <table className="w-full text-sm border rounded">
      <thead className="text-xs text-gray-500 bg-gray-50">
        <tr>
          <th className="text-left px-3 py-2">Ticker</th>
          <th className="text-left px-3 py-2">Market</th>
          <th className="text-right px-3 py-2">Price</th>
          <th className="text-left px-3 py-2">Notes</th>
          <th className="px-3 py-2"></th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const key = `${it.ticker}:${it.market}`;
          const price = prices[key];
          const disabled = busy === key;
          return (
            <tr key={key} className="border-t">
              <td className="px-3 py-2 font-mono">{it.ticker}</td>
              <td className="px-3 py-2">{it.market}</td>
              <td className="px-3 py-2 text-right">
                {fmt(price?.currency, price?.price)}
              </td>
              <td className="px-3 py-2 text-gray-600">{it.notes ?? ""}</td>
              <td className="px-3 py-2 text-right">
                <button
                  onClick={() => handleDelete(it)}
                  disabled={disabled}
                  className="text-red-500 disabled:opacity-50"
                >
                  🗑
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 3: Real watchlist tab**

Replace `frontend/components/watchlist-tab.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listWatchlists, listWatchlistItems, addWatchlistItem,
  type Watchlist, type WatchlistItem,
} from "@/lib/api-watchlists";
import { batchPrices, refreshPrices, type PriceQuote } from "@/lib/api-prices";
import type { Market } from "@/lib/api-portfolios";
import { WatchlistSelector } from "./watchlist-selector";
import { WatchlistTable } from "./watchlist-table";
import { RefreshButton } from "./refresh-button";
import { TickerAutocomplete } from "./ticker-autocomplete";

export function WatchlistTab() {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [prices, setPrices] = useState<Record<string, PriceQuote>>({});
  const [adding, setAdding] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadWatchlists = useCallback(async () => {
    const wl = await listWatchlists();
    setWatchlists(wl);
    if (wl.length > 0 && !selectedId) setSelectedId(wl[0].id);
  }, [selectedId]);

  const loadItems = useCallback(async () => {
    if (!selectedId) {
      setItems([]); setPrices({});
      return;
    }
    const its = await listWatchlistItems(selectedId);
    setItems(its);
    if (its.length > 0) {
      const pairs = its.map(i => [i.ticker, i.market] as [string, Market]);
      const r = await batchPrices(pairs);
      setPrices(r.prices);
    } else {
      setPrices({});
    }
  }, [selectedId]);

  useEffect(() => { void loadWatchlists(); }, [loadWatchlists]);
  useEffect(() => { void loadItems(); }, [loadItems]);

  async function handleRefresh() {
    if (items.length === 0) return;
    const pairs = items.map(i => [i.ticker, i.market] as [string, Market]);
    const r = await refreshPrices(pairs);
    setPrices(r.prices);
  }

  async function handleAdd(picked: { ticker: string; market: Market }) {
    if (!selectedId) return;
    try {
      await addWatchlistItem(selectedId, picked.ticker, picked.market, notes || undefined);
      setNotes("");
      setAdding(false);
      await loadItems();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (watchlists.length === 0) {
    return (
      <div className="border-dashed border rounded p-8 text-center">
        <p className="text-gray-500 mb-3">No watchlists yet.</p>
        <WatchlistSelector
          watchlists={[]} selectedId={null}
          onSelect={setSelectedId}
          onCreate={(w) => { setWatchlists([w]); setSelectedId(w.id); }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <WatchlistSelector
          watchlists={watchlists}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreate={(w) => { setWatchlists([...watchlists, w]); setSelectedId(w.id); }}
        />
        <div className="flex gap-2">
          <RefreshButton onRefresh={handleRefresh} />
          <button
            onClick={() => setAdding(!adding)}
            className="border rounded px-3 py-1 bg-blue-500 text-white"
          >
            + Add ticker
          </button>
        </div>
      </div>

      {adding && (
        <div className="border rounded p-3 space-y-2">
          <TickerAutocomplete autoFocus onPick={(r) => handleAdd(r)} />
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            className="border rounded px-2 py-1 w-full"
          />
        </div>
      )}

      {error && <div className="bg-red-50 text-red-700 px-3 py-2 rounded">{error}</div>}

      {selectedId && (
        <WatchlistTable
          items={items} prices={prices}
          watchlistId={selectedId}
          onDeleted={loadItems}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Build smoke test**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run build
```

Expected: success.

- [ ] **Step 5: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "feat(frontend): Watchlist tab — CRUD + live prices

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Playwright setup + 3 E2E browser tests

**Files:**
- Modify: `frontend/package.json` (add @playwright/test)
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/portfolio.spec.ts`
- Create: `frontend/e2e/helpers/auth.ts`

**Goal:** Verify the critical paths in a real browser. The tests need a live backend + frontend stack running.

- [ ] **Step 1: Install Playwright**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm install -D @playwright/test && npx playwright install chromium
```

- [ ] **Step 2: Playwright config**

Create `frontend/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  retries: 0,
  reporter: "list",
});
```

- [ ] **Step 3: Auth helper**

Create `frontend/e2e/helpers/auth.ts`:

```ts
import type { Page } from "@playwright/test";

/**
 * Inject a Supabase session into localStorage so the page treats
 * the user as logged in. The session token must be obtained out-of-band
 * (e.g., via the Supabase password-grant endpoint) and exported as
 * PLAYWRIGHT_SESSION_JSON.
 */
export async function loginAsTestUser(page: Page): Promise<void> {
  const sessionJson = process.env.PLAYWRIGHT_SESSION_JSON;
  if (!sessionJson) {
    throw new Error("PLAYWRIGHT_SESSION_JSON env var must be set");
  }
  await page.addInitScript((s) => {
    window.localStorage.setItem("sb-stylobate-auth-token", s);
  }, sessionJson);
}
```

The exact localStorage key depends on the Supabase client config — for `createBrowserClient` with the default storage adapter, the key is `sb-<project-ref>-auth-token`. Look at `frontend/lib/api-base.ts` and the running app's localStorage in DevTools to confirm the key name, then update the helper.

- [ ] **Step 4: The three tests**

Create `frontend/e2e/portfolio.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { loginAsTestUser } from "./helpers/auth";

test.describe("Portfolio", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page);
    await page.goto("/portfolio");
  });

  test("add a position, see it in USD cohort, delete it", async ({ page }) => {
    // If no portfolio exists, create one
    if (await page.getByText("No portfolios yet").isVisible({ timeout: 2000 }).catch(() => false)) {
      await page.getByPlaceholder("Portfolio name").fill("Playwright Test");
      await page.getByRole("button", { name: "Create" }).click();
    }

    await page.getByRole("button", { name: "+ Add position" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();
    await page.getByLabel("Quantity").fill("10");
    await page.getByLabel(/Cost basis/).fill("175");
    await page.getByRole("button", { name: "Save position" }).click();

    // AAPL row appears within the USD (Equities & ETFs) cohort
    const cohortHeader = page.getByText(/USD \(Equities & ETFs\) — 1 positions/);
    await expect(cohortHeader).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("AAPL")).toBeVisible();

    // Delete it
    page.once("dialog", (d) => d.accept());  // confirm() prompt
    await page.getByRole("button", { name: "🗑" }).first().click();
    await expect(page.getByText("AAPL")).not.toBeVisible({ timeout: 5000 });
  });

  test("refresh updates as_of", async ({ page }) => {
    // Assume previous test left an AAPL position OR add one here
    await page.getByRole("button", { name: "+ Add position" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();
    await page.getByLabel("Quantity").fill("5");
    await page.getByLabel(/Cost basis/).fill("180");
    await page.getByRole("button", { name: "Save position" }).click();
    await expect(page.getByText("AAPL")).toBeVisible({ timeout: 5000 });

    await page.getByRole("button", { name: /Refresh prices/ }).click();
    // Loading state shows briefly
    await expect(page.getByRole("button", { name: /Refresh|Refreshing/ })).toBeVisible();

    // Cleanup
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "🗑" }).first().click();
  });

  test("watchlist: add NVDA, see price, delete", async ({ page }) => {
    await page.getByRole("button", { name: "Watchlist" }).click();

    if (await page.getByText("No watchlists yet").isVisible({ timeout: 2000 }).catch(() => false)) {
      await page.getByPlaceholder("Watchlist name").fill("Playwright Watchlist");
      await page.getByRole("button", { name: "Create" }).click();
    }

    await page.getByRole("button", { name: "+ Add ticker" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("NVIDIA");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();

    await expect(page.getByText("NVDA")).toBeVisible({ timeout: 8000 });

    // Cleanup
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "🗑" }).first().click();
    await expect(page.getByText("NVDA")).not.toBeVisible({ timeout: 5000 });
  });
});
```

- [ ] **Step 5: Capture a session token for the test runner**

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
SESSION_JSON=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}')
export PLAYWRIGHT_SESSION_JSON="$SESSION_JSON"
echo "session captured (${#SESSION_JSON} bytes)"
```

- [ ] **Step 6: Run Playwright against the local stack**

In one terminal:

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1
```

In another:

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run dev
```

In a third (after both are up):

```bash
cd /Users/rakhisinha/Stylobate/frontend && npx playwright test
```

Expected: 3 passed.

If they fail, capture the failure mode:
- Wrong localStorage key → update `helpers/auth.ts`.
- Modal selector mismatches → adjust `getByPlaceholder` / `getByLabel` queries.
- Long yfinance cold-cache → increase test timeout in `playwright.config.ts`.

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "test(frontend): Playwright setup + 3 E2E browser tests for portfolio + watchlist

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Live UAT against deployed stack

**Files:** none — verification only.

**Goal:** Verify the live deployed stack (Render backend + Vercel frontend) produces correct cohort grouping with USD + INR + crypto positions.

- [ ] **Step 1: Confirm Render + Vercel are healthy**

```bash
curl -s https://stylobate-backend.onrender.com/healthz
```

Expected: `{"status":"ok","service":"stylobate-backend"}`.

Open `https://stylobate.vercel.app/portfolio` in a browser. Expected: page loads (after login). If `/portfolio` 404s, Vercel hasn't redeployed — check the Vercel dashboard.

- [ ] **Step 2: Add a US position via the UI**

Login → `/portfolio` → `+ Add position` → ticker "AAPL" → resolve → use → quantity 10 → cost 175 → save.

Expected: AAPL row appears in the "USD (Equities & ETFs)" cohort with a current price (~$189 area). Cohort card shows 1 position.

- [ ] **Step 3: Add an Indian position**

`+ Add position` → ticker "Reliance" → resolve to `RELIANCE.NS` → use → quantity 100 → cost 1250 → save.

Expected: A new INR cohort header appears with the Reliance row. INR cohort card shows 1 position with a positive P/L.

- [ ] **Step 4: Add a crypto position**

`+ Add position` → ticker "BTC" → resolve → use → quantity 0.5 → cost 42000 → save.

Expected: A new "USD (Crypto)" cohort header appears (separate from the USD-equity cohort). BTC row shows a current price near $80k.

- [ ] **Step 5: Click Refresh**

Click `⟳ Refresh prices`.

Expected: All three cohorts re-render. The button shows "Refreshing..." briefly.

- [ ] **Step 6: Edit and delete**

Click ✎ on the AAPL row → change quantity to 15 → save. Expected: row updates.

Click 🗑 on the BTC row → confirm. Expected: row disappears, USD (Crypto) cohort disappears.

- [ ] **Step 7: Watchlist**

Switch to Watchlist tab → `+ New` → "Daily Movers" → Create → `+ Add ticker` → NVDA → resolve → use → save.

Expected: NVDA row with a current price.

- [ ] **Step 8: Cross-user RLS check**

Open an incognito window, sign in as a second user (or temporarily change the password). Navigate to `/portfolio`. Expected: the user-A portfolios are NOT visible.

If you don't have a second test user, skip this step and note it in the report.

- [ ] **Step 9: Stop any local servers, confirm green CI**

Verify Render's "Deploys" tab shows the most recent commit as live and successful.

## Report

Document for the controller:
- Which UAT steps passed.
- Which positions appeared with which prices (sanity-check Bitcoin and Reliance prices vs reality).
- Whether the cohort grouping matched expectation (USD-equity vs USD-crypto as separate cohorts).
- Any UX rough edges that should be filed as follow-ups (not Phase 3A scope).

---

## End-of-Phase 3A acceptance checklist

- [ ] Backend tests: ~123 passing (`uv run pytest -q`)
- [ ] ruff + mypy strict clean
- [ ] Playwright: 3 tests pass locally
- [ ] Live UAT on deployed stack: USD + INR + CRYPTO positions render in correct cohorts with live prices
- [ ] `https://stylobate.vercel.app/portfolio` works end-to-end for the test user
- [ ] All 12 task commits on `main`, pushed to GitHub

Phase 3B (Portfolio Strategist agent) builds on this DB shape.


