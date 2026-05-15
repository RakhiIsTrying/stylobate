# Stylobate — Phase 3A: Portfolio CRUD Design

> **For agentic workers:** This is a sub-phase spec. After approval, invoke `superpowers:writing-plans` to produce the implementation plan at `docs/superpowers/plans/`.

**Parent spec:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` (§4.3 Decision specialists, §5.2 Portfolio tools, §7 Database schema, §15 Phase 3 rollout).

**Goal:** Ship the portfolio + watchlist CRUD UI and backend. Users can create portfolios, add/edit/delete positions (US equities, ETFs, Indian equities/ETFs, crypto), and see them in a per-currency-cohort grouped table with live prices. Watchlists work the same way without quantity/cost. No agents yet — that's 3B (Portfolio Strategist) and 3C (Risk Manager).

**Architecture:** New `/portfolio` route in Next.js with two tabs (Holdings, Watchlist). FastAPI exposes REST CRUD plus a batched live-prices endpoint backed by the existing `cache_kv` table (15-min TTL). Same JWKS auth as `/chat/stream`; RLS on existing tables is the second line of defense. No SSE, no new agents, no chat integration.

**Tech Stack:** Existing stack (Next.js frontend, FastAPI backend, Supabase Postgres + auth, yfinance for prices). New backend deps: none (cache_kv is already in schema). New frontend deps: Playwright for E2E tests.

**Out of scope (deferred to 3B/3C+):**
- Portfolio Strategist agent (`calc_portfolio_stats`, `suggest_rebalance`, `tax_lot_view`).
- Risk Manager agent (`concentration_check`, `calc_var`, `stress_test`, `get_correlations`).
- Multi-lot UI per position (3A stores `cost_basis` as weighted average only; tax_lots table stays empty).
- Chat integration ("ask Stylobate about my portfolio") — comes with 3B.
- CSV bulk upload — single-position form only in 3A.
- Component unit tests (Vitest) — Playwright covers the critical paths.

---

## 1. Architecture overview

`/portfolio` is a Next.js page with two tabs:

- **Holdings** — primary tab. Multi-portfolio selector + create-new dialog + grouped position table + add-position modal + refresh-prices button.
- **Watchlist** — secondary tab. Multi-watchlist selector + create-new dialog + simpler table (no qty/cost) + add-item flow.

Backend routes (under `backend/app/routes/portfolios.py`, `watchlists.py`, `prices.py`):

| Method | Path | Purpose |
|---|---|---|
| GET    | `/portfolios`                    | list user's portfolios |
| POST   | `/portfolios`                    | create portfolio (name, base_currency) |
| PATCH  | `/portfolios/{id}`               | rename / change base_currency |
| DELETE | `/portfolios/{id}`               | drop portfolio (cascade) |
| GET    | `/portfolios/{id}/positions`     | list positions WITH live prices joined |
| POST   | `/portfolios/{id}/positions`     | add position |
| PATCH  | `/positions/{id}`                | edit position |
| DELETE | `/positions/{id}`                | drop position |
| GET    | `/watchlists`                    | list watchlists |
| POST   | `/watchlists`                    | create watchlist |
| PATCH  | `/watchlists/{id}`               | rename |
| DELETE | `/watchlists/{id}`               | drop watchlist |
| GET    | `/watchlists/{id}/items`         | list items with live prices |
| POST   | `/watchlists/{id}/items`         | add item |
| DELETE | `/watchlists/{id}/items/{ticker_market}` | drop item |
| GET    | `/prices?tickers=AAPL:US,BTC:CRYPTO` | batch live prices (for watchlist) |
| POST   | `/prices/refresh`                | invalidate keys + refetch |
| POST   | `/resolve`                       | resolve free-text → typed `TickerResolution` (used by Add-position autocomplete) |

Auth: every route uses the existing `Depends(get_current_user)` JWKS dependency. RLS policies (already in `20260514000002_rls_policies.sql`) enforce `user_id = auth.uid()` at the database level too.

The `/resolve` route is a thin wrapper around `app.agents.ticker_resolver.resolve_ticker` (already implemented in Phase 1). It currently runs inside `/chat/stream`; 3A extracts it as a standalone endpoint so the Add-position modal can autocomplete without starting a chat turn. Same JWKS auth; same `TickerResolution` shape; cached for 24h per query (resolver runs a small Haiku call — cheap but worth caching).

Multiple portfolios per user is supported (selector + create dialog). First-time visitor sees an empty state with `[+ Create your first portfolio]` — no auto-create magic, user picks the name (e.g., "Taxable", "IRA", "Crypto Wallet").

## 2. Data model — what's already there

The Phase 0 migration (`20260514000001_initial_schema.sql`) created these tables — they don't change in 3A:

```sql
portfolios(id, user_id, name, base_currency, created_at, updated_at)
positions(id, portfolio_id, ticker, market, asset_class, quantity, cost_basis, currency, opened_at, created_at)
tax_lots(id, position_id, qty, price, currency, acquired_at)    -- unused in 3A
watchlists(id, user_id, name, created_at)
watchlist_items(watchlist_id, ticker, market, added_at, notes)
cache_kv(key, value, expires_at)
```

3A only writes to `portfolios`, `positions`, `watchlists`, `watchlist_items`, `cache_kv`. `tax_lots` is left empty for 3A — 3B's Portfolio Strategist treats each position as a single virtual lot when computing gains.

**Validation rules at write time** (FastAPI Pydantic models reject invalid input before it hits the DB):

- `market ∈ {US, IN, CRYPTO}`
- `asset_class ∈ {equity, etf, crypto}`
- `(market = CRYPTO) ⇒ (asset_class = crypto)`
- `quantity > 0`
- `cost_basis ≥ 0`
- `currency` is a 3-letter ISO code; for `market=US` defaults to USD; for `market=IN` defaults to INR; for `market=CRYPTO` defaults to USD. User can override at write time (e.g., user holds AAPL via London in GBP — unusual but allowed).
- Ticker uniqueness: `(portfolio_id, ticker, market)` is unique. Buying more shares of an existing position UPSERTs and recomputes weighted-average cost basis: `new_basis = (old_qty * old_basis + add_qty * add_basis) / (old_qty + add_qty)`, `new_qty = old_qty + add_qty`. `opened_at` stays the earliest date.

## 3. Live pricing

A new module `backend/app/data/prices_cache.py` exposes:

```python
async def get_prices(tickers: list[tuple[str, str]]) -> dict[tuple[str, str], Price]:
    """Get prices for [(ticker, market), ...]. Returns dict keyed by (ticker, market)."""
```

Algorithm:

1. Build keys `f"price:{ticker}:{market}"` for each pair.
2. Read from `cache_kv` where `expires_at > now()`.
3. For misses, call `fetch_ticker_info(ticker, market=market)` from the Phase 1 adapter — concurrent via `asyncio.gather`.
4. Upsert misses into `cache_kv` with `expires_at = now() + interval '15 minutes'`.
5. Return merged hits + fresh.

```python
async def invalidate_prices(tickers: list[tuple[str, str]]) -> None:
    """Delete the keys (refresh button)."""
```

The positions GET response embeds prices in each position row so the frontend does one round trip per page load:

```json
{
  "portfolio_id": "<uuid>",
  "positions": [
    {"id":"...", "ticker":"AAPL", "market":"US", "quantity":50, "cost_basis":175.0, "currency":"USD",
     "current_price":189.42, "price_currency":"USD", "as_of":"2026-05-15T14:23:11Z", "pl_pct":8.24, "value_native":9471.0},
    ...
  ],
  "cohorts": [
    {"currency":"USD","positions_count":3,"total_cost_native":24500.0,"total_value_native":27536.0,"pl_pct":12.4,"as_of":"2026-05-15T14:23:11Z"},
    ...
  ]
}
```

The frontend renders the table grouped by cohort directly from the `cohorts` array. P/L percent is computed server-side per cohort: `sum(value_native - cost_native) / sum(cost_native) * 100`.

## 4. Frontend pages

### `/portfolio` (root route)

```
┌── Top tabs ──┐
│ [Holdings*] [Watchlist]              │
├──────────────┴───────────────────────┤
│ Portfolio: [My Portfolio ▼]  [+ New] │
│                          [⟳ Refresh] │
├──────────────────────────────────────┤
│ ┌─USD ────┐ ┌─INR ────┐ ┌─CRYPTO──┐  │  cohort cards rendered dynamically from the
│ │ Eq/ETF  │ │ Eq/ETF  │ │ (USD)   │  │  cohorts array — one per non-empty cohort
│ │$24,500  │ │₹285,000 │ │$40,326  │  │
│ │+12.4%   │ │+8.1%    │ │+92.0%   │  │
│ └─────────┘ └─────────┘ └─────────┘  │
│                                      │
│ ── USD — 3 positions — $24,500 ──    │
│   AAPL    50   $175   $189   +8.0% ✏️🗑│
│   MSFT    20   $410   $445   +8.5% ✏️🗑│
│   SPY     10   $440   $498  +13.2% ✏️🗑│
│ ── INR — 2 positions — ₹285,000 ──   │
│   RELIANCE.NS 100 ₹1,250 ₹1,384 +10.7%│
│   TCS.NS      15  ₹3,800 ₹4,025  +5.9%│
│ ── CRYPTO (USD) — 1 position — $40K ─│
│   BTC     0.5  $42K   $80,652 +92.0%  │
│                                      │
│ [+ Add position]                     │
└──────────────────────────────────────┘
```

Empty state (no portfolios): "Welcome to portfolio tracking. [+ Create your first portfolio]".

Empty state (portfolio with no positions): "No positions yet. [+ Add position]".

### Add-position modal

Five fields:

1. **Ticker** — autocomplete via the existing Phase 1 resolver. The resolver returns `TickerResolution{ticker, name, market, asset_class, confidence}` — modal pre-fills `market` and `asset_class` from this result; user can't change them (re-pick ticker if wrong).
2. **Quantity** — numeric input. Accepts up to 8 decimals (for crypto).
3. **Cost basis** — numeric input. Label dynamically shows the inferred currency.
4. **Currency** — pre-filled from market default. Overridable via a small disclosure.
5. **Opened on** — date picker, defaults to today.

Submit calls `POST /portfolios/{id}/positions`. On success, the row appears in the table optimistically. On UPSERT (existing ticker), the row updates with new weighted-avg cost.

### Edit modal

Same fields, pre-filled. Ticker is read-only (delete + re-add to change a ticker).

### Watchlist tab

```
┌──────────────────────────────────────┐
│ Watchlist: [Daily Movers ▼]  [+ New] │
│                          [⟳ Refresh] │
├──────────────────────────────────────┤
│ Ticker      Market  Price   Notes ✏️🗑│
│ NVDA         US     $890   "AI ply"   │
│ HDFCBANK.NS  IN     ₹1,650 ""         │
│ ETH          CRYPTO $3,420 "L2 thesis"│
│ [+ Add ticker]                       │
└──────────────────────────────────────┘
```

Add-item: ticker autocomplete + optional notes textarea. Notes are inline-editable in the table (click → edit → blur saves).

## 5. Cohort labeling

A cohort is uniquely identified by `currency` (not by market). Why: the user wanted "no FX conversion" but they may hold US stocks in USD AND crypto priced in USD. Combining USD-equity and USD-crypto into the same cohort overstates concentration. So:

- Cohort key: `(currency, asset_class_group)` where `asset_class_group` is `equity_etf` for {equity, etf} and `crypto` for {crypto}.
- Display labels: "USD (Equities & ETFs)", "INR (Equities & ETFs)", "CRYPTO (USD)".
- Within a cohort, sort positions by descending current value.

This shows up in the cohort cards and the table headers. Behind the scenes the cohort key is `(currency, asset_class_group)`. The Phase 3B Portfolio Strategist will use the same grouping when computing weights.

## 6. Auth + RLS

The existing JWKS dependency `get_current_user` returns `(user_id, jwt_payload)`. CRUD endpoints scope every query by `user_id`. RLS policies in `20260514000002_rls_policies.sql` already enforce this at the DB layer for `portfolios`, `positions`, `tax_lots`, `watchlists`, `watchlist_items`.

The backend connects to Supabase via service-role for these endpoints (RLS still applies because we explicitly set the JWT claim via `SET LOCAL request.jwt.claims = '...'` on each connection). For 3A this can be simplified: use service-role + explicit `where user_id = $1` filter on every query, double-checked against the JWT. This matches the existing chat path.

## 7. Error handling

| Failure | Response | Frontend behaviour |
|---|---|---|
| Missing/invalid JWT | 401 | redirect to `/login` |
| User not found (JWT valid, user deleted) | 401 | redirect to `/login` |
| Portfolio not found OR not owned by user | 404 | toast "Portfolio not found", redirect to `/portfolio` |
| Position validation error (negative qty, etc.) | 422 with field errors | inline errors in modal |
| yfinance rate-limit or transient error | partial response: positions are returned without `current_price`; `as_of` omitted; `cohorts` totals show "cost basis" only with a flag `prices_partial=true` | banner: "Prices temporarily unavailable. Click ⟳ to retry." |
| cache_kv write fails | log warning, still return fresh prices | transparent to user (next page load may re-fetch) |
| Network blip on submit | error in toast, modal stays open with values | user retries |

The yfinance partial-availability case is the most important — Phase 2 showed yfinance occasionally rate-limits or returns empty. The portfolio page must stay usable when prices are missing.

## 8. Performance targets

- Page load (warm cache, 10 positions): < 300ms backend, < 600ms total including render.
- Page load (cold cache, 10 positions): < 3s backend (10 yfinance calls in parallel @ ~250ms each), < 3.5s total.
- Refresh button: same as cold cache.
- Add position: < 500ms backend (1 yfinance call + 1 INSERT), < 800ms total.

These are budgets, not guarantees. If we miss them in the live E2E, we file a 3A-postscript task to optimize.

## 9. Testing

### Backend (pytest)

| File | What it tests |
|---|---|
| `test_routes_portfolios.py` | CRUD endpoints; auth required; RLS enforced (user A can't read user B's portfolio) |
| `test_routes_positions.py` | CRUD + validation (market enum, qty>0, currency 3-letter, UPSERT recomputes weighted-avg basis) |
| `test_routes_watchlists.py` | CRUD + auth |
| `test_routes_prices.py` | Batch GET with cache hits, misses, mixed; refresh deletes + refetches |
| `test_prices_cache.py` | Pure unit — yfinance mocked, cache_kv mocked; verifies TTL + on-conflict-update logic |
| `test_routes_e2e_portfolio.py` | Async: create portfolio → add 3 positions (USD/INR/crypto) → GET → assert cohorts shape, prices present |

yfinance mock: `patch("app.data.prices_cache._fetch_ticker_info")` returning `TickerInfo` fixtures. Mirrors the Phase 2 mocking pattern.

### Frontend (Playwright)

Three end-to-end browser tests at `frontend/e2e/portfolio.spec.ts`:

1. **Add → display → delete**: log in, add a USD AAPL position, see it in the USD cohort, delete it, cohort disappears.
2. **Refresh**: add a position, click ⟳, verify the `as_of` timestamp updates.
3. **Watchlist add**: switch to Watchlist tab, add NVDA, verify it appears with a live price.

Playwright config + setup is its own plan task. The first test doubles as a smoke for everything in 3A.

### Live E2E (final plan task)

Same shape as Phase 2C: run backend + frontend locally, log in, add a US position (AAPL), an Indian position (RELIANCE.NS), and a crypto position (BTC), verify the grouped table renders, cohort cards show correct per-currency totals, refresh button updates `as_of` timestamps, edit and delete work.

## 10. File map (preview — concrete in writing-plans)

```
backend/
  app/
    routes/
      portfolios.py          NEW — CRUD + positions GET (with prices joined)
      watchlists.py          NEW — CRUD
      prices.py              NEW — batch GET + refresh POST
      resolve.py             NEW — POST /resolve (extracts Phase 1 ticker resolver)
    data/
      prices_cache.py        NEW — get_prices, invalidate_prices, write_cache_rows
    models/
      portfolio.py           NEW — Pydantic models (PortfolioIn, PositionIn, PositionOut, CohortSummary, etc.)
      watchlist.py           NEW — Pydantic models
    db/
      portfolios.py          NEW — sql helpers (async; raw queries via asyncpg)
      watchlists.py          NEW
      prices.py              NEW — cache_kv access
    main.py                  MODIFY — register new routers
  tests/
    test_routes_portfolios.py    NEW
    test_routes_positions.py     NEW
    test_routes_watchlists.py    NEW
    test_routes_prices.py        NEW
    test_prices_cache.py         NEW
    test_routes_e2e_portfolio.py NEW
frontend/
  app/
    portfolio/
      page.tsx                NEW — main page with tabs
      [portfolioId]/page.tsx  NEW — scoped to one portfolio
  components/
    portfolio-table.tsx       NEW — grouped table by cohort
    position-row.tsx          NEW
    add-position-modal.tsx    NEW
    edit-position-modal.tsx   NEW
    portfolio-selector.tsx    NEW
    cohort-card.tsx           NEW
    watchlist-table.tsx       NEW
    add-watchlist-item.tsx    NEW
    refresh-button.tsx        NEW
  lib/
    api-portfolios.ts         NEW — typed fetch wrappers for the new endpoints
    api-watchlists.ts         NEW
    api-prices.ts             NEW
  e2e/
    portfolio.spec.ts         NEW — 3 Playwright tests
  playwright.config.ts        NEW
  package.json                MODIFY — +@playwright/test
```

(no schema changes; tables already exist)

## 11. End-of-3A acceptance

- Backend: full suite green (`uv run pytest -q`) at ~105-110 tests (92 today + ~15 new).
- ruff + mypy strict clean.
- Playwright: 3 tests pass locally and against the deployed Vercel + Render stack.
- Live UAT on local stack: USD/INR/CRYPTO positions display correctly in cohort groups with live prices; add/edit/delete works; refresh updates `as_of`; watchlist add works.
- Render + Vercel auto-deploys go green on `main`.
- Deployed URL: `https://stylobate.vercel.app/portfolio` is reachable for the logged-in user.

Phase 3B (Portfolio Strategist agent) takes this DB shape as a given and adds the analysis layer.
