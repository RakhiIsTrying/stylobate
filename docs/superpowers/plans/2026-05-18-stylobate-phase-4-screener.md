# Stylobate — Phase 4: Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Screener — a Sonnet specialist triggered by `/chat/stream` keyword detection that turns natural-language themes ("AI infrastructure plays in India", "cheap US dividend stocks") into typed candidate ticker lists, backed by curated SP500 / Nifty 500 / CoinGecko top-100 universes with grounded LLM theme-matching.

**Architecture:** Mirrors the Phase 3B/3C specialist pattern — pure data module + closure-bound Tool factory + 8-turn Sonnet agent + dedicated SSE endpoint. The keyword router in `chat.py` gains a third branch (after portfolio, before ticker-deep-dive). The agent runs without Lead Banker — its output is self-contained.

**Tech Stack:** Existing — FastAPI, Pydantic v2, Anthropic Sonnet 4.6, yfinance, cache_kv. New runtime dep: **none** (CoinGecko via `httpx` which is already in the env). Dev-time-only dep: `pandas`+`lxml` used by `scripts/build_universes.py` to seed the JSONs from Wikipedia ONCE per quarter; not imported by the app.

**Spec reference:** `docs/superpowers/specs/2026-05-18-stylobate-phase-4-screener-design.md`. Every spec section maps to one or more tasks below.

---

## File map

```
backend/
  data/
    universes/
      sp500.json                       T1 — committed asset
      nifty500.json                    T1 — committed asset
  scripts/
    build_universes.py                 T1 — dev-time bootstrap; not imported
  app/
    data/
      universes.py                     T2 — runtime loader + CoinGecko fetcher
    models/
      screener.py                      T3 — Pydantic models
    tools/
      screener.py                      T3 — 4 closure-bound Tool wrappers
    agents/
      screener.py                      T4 — Sonnet agent
    prompts/
      screener.md                      T4 — agent system prompt
    routes/
      chat_screen.py                   T5 — POST /chat/screen direct endpoint
      chat.py                          T6 — add is_screener_query + routing
    main.py                            T5 — register chat_screen router
  tests/
    test_data_universes.py             T2
    test_tools_screener.py             T3
    test_screener_agent.py             T4
    test_routes_chat_screen_endpoint.py T5
    test_routes_chat_stream_screener.py T6
    test_routes_e2e_screener.py        T7
```

---

## Glossary

- **Universe** = a named ticker list. Phase 4 ships three: `sp500` (S&P 500 large-caps), `nifty500` (Indian large+mid caps), `crypto` (CoinGecko top 100 by market cap).
- **Constituent** = `{ticker, name, sector?, currency, asset_class, market}`. Static for sp500/nifty500 (committed JSON); live for crypto (24h cache).
- **Grounded theme matching** = the LLM picks tickers from the loaded constituent list. The tool VALIDATES every returned ticker exists in the universe; invented tickers are dropped with a note.
- **CoinGecko free API** = `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1`. No auth, 30 req/min limit. Cached 24h in `cache_kv` (key `universe:crypto:top100`).
- **Keyword router ordering** = `chat.py` checks `is_portfolio_query` → `is_screener_query` → ticker-resolve. Portfolio queries with "screen my portfolio" hit the portfolio path first (more specific).

---

## Pre-flight

- [ ] **Step P1: Baseline tests pass**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest -q
```

Expected: 178 passing (Phase 3C end state).

- [ ] **Step P2: Lint + types clean**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests
```

Expected: `All checks passed!` + `Success: no issues found in 100 source files`.

- [ ] **Step P3: yfinance + cache_kv adapters in place**

```bash
ls /Users/rakhisinha/Stylobate/backend/app/data/yfinance_adapter.py \
   /Users/rakhisinha/Stylobate/backend/app/db/prices.py 2>&1
```

Expected: both print.

---

## Task 1: Bootstrap script + universe JSONs

**Files:**
- Create: `backend/scripts/build_universes.py`
- Create: `backend/data/universes/sp500.json`
- Create: `backend/data/universes/nifty500.json`

**Goal:** A one-time dev script that scrapes Wikipedia + commits two static JSONs. The script lives in `scripts/` and is NEVER imported by the runtime app — it's a shipping artifact. `pandas` + `lxml` are dev deps only.

- [ ] **Step 1: Write the script**

Create `backend/scripts/build_universes.py`:

```python
#!/usr/bin/env python
"""Bootstrap S&P 500 + Nifty 500 universe JSONs from Wikipedia.

Run quarterly (or as needed) to refresh constituent lists:

    cd backend
    uv pip install --no-deps pandas lxml
    uv run python scripts/build_universes.py

The script writes `data/universes/sp500.json` and `data/universes/nifty500.json`.
Commit the resulting JSONs to git. This script itself is NEVER imported by the
running app — pandas/lxml are NOT runtime dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-not-found]


def _slug_to_ticker(symbol: str, suffix: str = "") -> str:
    """Normalise to a yfinance-compatible ticker."""
    t = symbol.strip().upper().replace(".", "-")
    return f"{t}{suffix}" if suffix else t


def _build_sp500() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    df = tables[0]
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        symbol = str(row["Symbol"])
        name = str(row["Security"])
        sector = str(row["GICS Sector"])
        out.append({
            "ticker": _slug_to_ticker(symbol),
            "name": name,
            "sector": sector,
            "currency": "USD",
            "asset_class": "equity",
            "market": "US",
        })
    return out


def _build_nifty500() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/NIFTY_500"
    tables = pd.read_html(url)
    df = tables[2] if len(tables) >= 3 else tables[0]
    if "Symbol" not in df.columns:
        for t in tables:
            if "Symbol" in t.columns:
                df = t
                break
        else:
            raise RuntimeError("Could not find Symbol column in Nifty 500 page")
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol or symbol == "nan":
            continue
        name = str(row.get("Company Name", row.get("Name", "")))
        sector = str(row.get("Sector", row.get("Industry", "")))
        if sector == "nan":
            sector = ""
        out.append({
            "ticker": _slug_to_ticker(symbol, suffix=".NS"),
            "name": name,
            "sector": sector or None,
            "currency": "INR",
            "asset_class": "equity",
            "market": "IN",
        })
    return out


def main() -> None:
    target_dir = Path(__file__).parent.parent / "data" / "universes"
    target_dir.mkdir(parents=True, exist_ok=True)

    sp500 = _build_sp500()
    nifty500 = _build_nifty500()

    sp500_path = target_dir / "sp500.json"
    nifty_path = target_dir / "nifty500.json"
    sp500_path.write_text(json.dumps(sp500, indent=2) + "\n")
    nifty_path.write_text(json.dumps(nifty500, indent=2) + "\n")

    print(f"SP500:    {len(sp500)} constituents -> {sp500_path}")
    print(f"Nifty500: {len(nifty500)} constituents -> {nifty_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Install dev deps (one-shot)**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv pip install --no-deps pandas lxml html5lib
```

`--no-deps` prevents pandas from pulling in numpy etc. into uv's lock — these stay venv-local, not in `pyproject.toml`.

- [ ] **Step 3: Run the script**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run python scripts/build_universes.py
```

Expected output (numbers may vary slightly with Wikipedia state):

```
SP500:    503 constituents -> /Users/rakhisinha/Stylobate/backend/data/universes/sp500.json
Nifty500: 500 constituents -> /Users/rakhisinha/Stylobate/backend/data/universes/nifty500.json
```

If the script errors out on Nifty 500 parsing (Wikipedia table structure varies), inspect `tables = pd.read_html(url)` indices interactively and adjust the column lookup. Required columns: ticker + name + sector. If sector isn't available, leave it None.

If Wikipedia is blocking from the network, fall back to a curated minimal seed: top-50 SP500 and top-30 Indian tech/consumer names. The runtime app doesn't care — it reads whatever JSON ships.

- [ ] **Step 4: Sanity-check the output**

```bash
cd /Users/rakhisinha/Stylobate/backend && python3 -c "
import json
for name in ['sp500', 'nifty500']:
    p = f'data/universes/{name}.json'
    rows = json.load(open(p))
    print(f'{name}: {len(rows)} rows; first=', rows[0])
    print(f'  sample tickers:', [r[\"ticker\"] for r in rows[:5]])
"
```

Expected: SP500 starts with names like AAPL, MSFT, GOOGL etc. Nifty500 has tickers ending in `.NS`.

- [ ] **Step 5: Commit (script + JSONs)**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/scripts/build_universes.py backend/data/universes/sp500.json backend/data/universes/nifty500.json && git commit -m "data(backend): seed S&P 500 + Nifty 500 universe JSONs + bootstrap script

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- The Nifty 500 Wikipedia table layout changes occasionally. If indices 0/1/2 don't work, print `[(i, t.columns.tolist()) for i, t in enumerate(tables)]` and pick the table that has Symbol + Company Name.
- mypy may not be run against `scripts/` — verify with `cd /Users/rakhisinha/Stylobate/backend && uv run mypy scripts/`. If it complains, prefix the file with `# type: ignore[import-not-found]` on the pandas import and accept the script is dev-only quality.
- `pandas`+`lxml` are intentionally NOT added to `pyproject.toml` runtime deps. Verify they don't leak in: `grep pandas pyproject.toml` should return nothing.

---

## Task 2: Universes runtime data layer

**Files:**
- Create: `backend/app/data/universes.py`
- Create: `backend/tests/test_data_universes.py`

**Goal:** Async functions to load the three universes. Static JSONs read from disk; CoinGecko fetched via httpx + 24h cache_kv. Returns lists of typed `Constituent` dicts.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_data_universes.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_load_static_universe_sp500() -> None:
    """SP500 JSON loads and returns Constituent-shaped dicts."""
    from app.data.universes import load_universe
    rows = await load_universe("sp500")
    assert len(rows) >= 100  # Wikipedia always has 500+ constituents
    sample = rows[0]
    assert "ticker" in sample
    assert "name" in sample
    assert sample["currency"] == "USD"
    assert sample["asset_class"] == "equity"
    assert sample["market"] == "US"


@pytest.mark.asyncio
async def test_load_static_universe_nifty500() -> None:
    from app.data.universes import load_universe
    rows = await load_universe("nifty500")
    assert len(rows) >= 100
    sample = rows[0]
    assert sample["currency"] == "INR"
    assert sample["asset_class"] == "equity"
    assert sample["market"] == "IN"
    assert sample["ticker"].endswith(".NS")


@pytest.mark.asyncio
async def test_load_universe_unknown_raises() -> None:
    from app.data.universes import load_universe
    with pytest.raises(ValueError, match="unknown universe"):
        await load_universe("nasdaq100")


@pytest.mark.asyncio
async def test_load_universe_crypto_cache_hit() -> None:
    now = datetime.now()
    cached_rows = [
        {"ticker": "BTC", "name": "Bitcoin", "sector": None,
         "currency": "USD", "asset_class": "crypto", "market": "CRYPTO",
         "market_cap": 1_500_000_000_000},
    ]
    rows = [{
        "key": "universe:crypto:top100",
        "value": cached_rows,
        "expires_at": now + timedelta(hours=12),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", AsyncMock()):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    assert len(result) == 1
    assert result[0]["ticker"] == "BTC"
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_load_universe_crypto_cache_miss_fetches_and_writes() -> None:
    fake_coingecko = [
        {"symbol": "btc", "name": "Bitcoin",
         "market_cap": 1_500_000_000_000},
        {"symbol": "eth", "name": "Ethereum",
         "market_cap": 400_000_000_000},
    ]
    fetch_mock = AsyncMock(return_value=fake_coingecko)
    write_mock = AsyncMock()
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", write_mock):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    assert [r["ticker"] for r in result] == ["BTC", "ETH"]
    assert all(r["currency"] == "USD" for r in result)
    assert all(r["asset_class"] == "crypto" for r in result)
    assert all(r["market"] == "CRYPTO" for r in result)
    fetch_mock.assert_awaited_once()
    write_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_universe_crypto_fallback_on_fetch_error() -> None:
    """CoinGecko 429 / network error returns the backup top-20 list."""
    fetch_mock = AsyncMock(side_effect=RuntimeError("429 rate limited"))
    with patch("app.data.universes._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.universes._fetch_coingecko_top100", fetch_mock), \
         patch("app.data.universes._write_cache_rows", AsyncMock()):
        from app.data.universes import load_universe
        result = await load_universe("crypto")
    tickers = [r["ticker"] for r in result]
    assert "BTC" in tickers
    assert "ETH" in tickers
    assert len(result) >= 5  # at least the backup list


def test_list_universes_names() -> None:
    from app.data.universes import list_universe_names
    names = list_universe_names()
    assert set(names) == {"sp500", "nifty500", "crypto"}
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_universes.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.universes'`.

- [ ] **Step 3: Implement `universes.py`**

Create `backend/app/data/universes.py`:

```python
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=24)
_KEY = "universe:crypto:top100"

_COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
)

# Used when CoinGecko is unreachable. Keep this list small + stable.
_CRYPTO_BACKUP: list[dict[str, Any]] = [
    {"ticker": "BTC", "name": "Bitcoin"},
    {"ticker": "ETH", "name": "Ethereum"},
    {"ticker": "USDT", "name": "Tether"},
    {"ticker": "BNB", "name": "BNB"},
    {"ticker": "SOL", "name": "Solana"},
    {"ticker": "XRP", "name": "XRP"},
    {"ticker": "USDC", "name": "USD Coin"},
    {"ticker": "ADA", "name": "Cardano"},
    {"ticker": "AVAX", "name": "Avalanche"},
    {"ticker": "DOGE", "name": "Dogecoin"},
]


def list_universe_names() -> list[str]:
    return ["sp500", "nifty500", "crypto"]


async def load_universe(name: str) -> list[dict[str, Any]]:
    """Return a flat list of constituent dicts. Raises ValueError on unknown."""
    if name == "crypto":
        return await _load_crypto_top100()
    if name == "sp500":
        return _load_static_universe("sp500.json")
    if name == "nifty500":
        return _load_static_universe("nifty500.json")
    raise ValueError(f"unknown universe: {name!r}")


def _load_static_universe(filename: str) -> list[dict[str, Any]]:
    path = (
        Path(__file__).parent.parent.parent / "data" / "universes" / filename
    )
    with path.open(encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise RuntimeError(f"{filename} did not contain a JSON array")
    return [dict(r) for r in rows]


async def _fetch_coingecko_top100() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_COINGECKO_URL)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError("CoinGecko returned non-array")
    return data


def _normalize_coingecko(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in raw:
        symbol = (r.get("symbol") or "").upper()
        if not symbol:
            continue
        out.append({
            "ticker": symbol,
            "name": r.get("name") or symbol,
            "sector": None,
            "currency": "USD",
            "asset_class": "crypto",
            "market": "CRYPTO",
            "market_cap": r.get("market_cap"),
        })
    return out


async def _load_crypto_top100() -> list[dict[str, Any]]:
    rows = await _fetch_cache_rows([_KEY])
    if rows:
        cached = rows[0]["value"]
        if isinstance(cached, list):
            return [dict(r) for r in cached]

    try:
        raw = await _fetch_coingecko_top100()
        normalized = _normalize_coingecko(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("CoinGecko fetch failed: %s — using backup list", e)
        return [
            {**r, "sector": None, "currency": "USD",
             "asset_class": "crypto", "market": "CRYPTO"}
            for r in _CRYPTO_BACKUP
        ]

    if not normalized:
        return [
            {**r, "sector": None, "currency": "USD",
             "asset_class": "crypto", "market": "CRYPTO"}
            for r in _CRYPTO_BACKUP
        ]

    expires = datetime.now(UTC) + _TTL
    try:
        await _write_cache_rows([(_KEY, normalized, expires)])
    except Exception as e:  # noqa: BLE001
        logger.warning("crypto universe cache write failed: %s", e)
    return normalized
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_universes.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 185 passing (178 prior + 7 new). ruff + mypy strict clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/data/universes.py backend/tests/test_data_universes.py && git commit -m "feat(backend): universes data layer (static JSON + CoinGecko cache_kv 24h)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- `httpx` should already be a runtime dep (used by `app/core/auth.py` for JWKS fetch). Verify with `grep httpx pyproject.toml`. If missing, add it.
- The cache key + TTL pattern matches `benchmarks.py` from Phase 3B. Match the existing style for `_fetch_cache_rows`/`_write_cache_rows` imports.
- The backup list MUST return ≥5 tickers so the test `test_load_universe_crypto_fallback_on_fetch_error` passes its `len(result) >= 5` assertion. The list above has 10 — safe.
- mypy strict on the `dict[str, Any]` rows — ensure `cached = rows[0]["value"]` is annotated correctly via the isinstance check.

---

## Task 3: Pydantic models + Screener tools

**Files:**
- Create: `backend/app/models/screener.py`
- Create: `backend/app/tools/screener.py`
- Create: `backend/tests/test_tools_screener.py`

**Goal:** 4 closure-bound tools backing the runtime data layer. Grounded `theme_to_universe` (drops invented tickers). Lazy `screen_stocks` (live yfinance fetch on shortlist only).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tools_screener.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import KeyRatios, TickerInfo


@pytest.mark.asyncio
async def test_resolve_universe_loads_sp500() -> None:
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "MSFT", "name": "Microsoft", "sector": "Information Technology",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]
    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        result = await resolve.impl(universes=["sp500"])
    assert result["universes"] == ["sp500"]
    assert len(result["constituents"]) == 2
    assert result["constituents"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_resolve_universe_rejects_unknown_name() -> None:
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    resolve = next(t for t in tools if t.name == "resolve_universe")
    result = await resolve.impl(universes=["nasdaq100"])
    assert "error" in result
    assert "nasdaq100" in result["error"]


@pytest.mark.asyncio
async def test_resolve_universe_combines_multiple() -> None:
    fake_sp500 = [{"ticker": "AAPL", "name": "Apple", "sector": "IT",
                   "currency": "USD", "asset_class": "equity", "market": "US"}]
    fake_nifty = [{"ticker": "TCS.NS", "name": "Tata Consultancy", "sector": "IT",
                   "currency": "INR", "asset_class": "equity", "market": "IN"}]

    async def fake_load(name: str) -> list[dict[str, Any]]:
        return {"sp500": fake_sp500, "nifty500": fake_nifty}[name]

    with patch("app.tools.screener.load_universe", side_effect=fake_load):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        result = await resolve.impl(universes=["sp500", "nifty500"])
    assert set(result["universes"]) == {"sp500", "nifty500"}
    tickers = {c["ticker"] for c in result["constituents"]}
    assert tickers == {"AAPL", "TCS.NS"}


@pytest.mark.asyncio
async def test_theme_to_universe_validates_against_loaded_universes() -> None:
    """LLM-supplied tickers that aren't in the loaded universes are dropped."""
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "NVDA", "name": "NVIDIA", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]
    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        theme = next(t for t in tools if t.name == "theme_to_universe")
        # Load the universe (populates the agent's internal cache)
        await resolve.impl(universes=["sp500"])
        # LLM picks AAPL (real), MADEUP (hallucinated), NVDA (real)
        result = await theme.impl(
            theme="AI semiconductor plays",
            tickers=["AAPL", "MADEUP", "NVDA"],
        )
    assert set(result["tickers"]) == {"AAPL", "NVDA"}
    assert "MADEUP" in result["dropped"]


@pytest.mark.asyncio
async def test_theme_to_universe_no_universe_loaded_returns_error() -> None:
    """If resolve_universe wasn't called first, theme tool returns an error."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    theme = next(t for t in tools if t.name == "theme_to_universe")
    result = await theme.impl(theme="X", tickers=["AAPL"])
    assert "error" in result


@pytest.mark.asyncio
async def test_screen_stocks_applies_filters() -> None:
    """Filters drop tickers; survivors include yfinance fundamentals."""
    fake_sp500 = [
        {"ticker": "AAPL", "name": "Apple", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "MSFT", "name": "Microsoft", "sector": "IT",
         "currency": "USD", "asset_class": "equity", "market": "US"},
        {"ticker": "WMT", "name": "Walmart", "sector": "Consumer Staples",
         "currency": "USD", "asset_class": "equity", "market": "US"},
    ]

    async def fake_info(ticker: str, market: str = "US") -> TickerInfo:
        prices = {"AAPL": 200.0, "MSFT": 450.0, "WMT": 80.0}
        return TickerInfo(
            ticker=ticker, name=ticker, currency="USD",
            market_cap=2_000_000_000_000, last_price=prices.get(ticker, 100.0),
        )

    async def fake_ratios(ticker: str, market: str = "US") -> KeyRatios:
        # AAPL high PE, MSFT high PE + high ROE, WMT cheap
        d = {
            "AAPL": KeyRatios(ticker="AAPL", pe_ttm=35.0, pb=50.0, ps_ttm=8.0,
                              roe_pct=120.0, roic_pct=40.0, debt_to_equity=1.5,
                              current_ratio=1.0, net_margin_pct=25.0,
                              rev_growth_yoy_pct=10.0),
            "MSFT": KeyRatios(ticker="MSFT", pe_ttm=32.0, pb=12.0, ps_ttm=12.0,
                              roe_pct=38.0, roic_pct=22.0, debt_to_equity=0.4,
                              current_ratio=1.7, net_margin_pct=36.0,
                              rev_growth_yoy_pct=12.0),
            "WMT": KeyRatios(ticker="WMT", pe_ttm=14.0, pb=5.0, ps_ttm=0.7,
                             roe_pct=18.0, roic_pct=10.0, debt_to_equity=1.7,
                             current_ratio=0.8, net_margin_pct=3.0,
                             rev_growth_yoy_pct=5.0),
        }
        return d[ticker]

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)), \
         patch("app.tools.screener.fetch_ticker_info", fake_info), \
         patch("app.tools.screener.fetch_key_ratios", fake_ratios):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        screen = next(t for t in tools if t.name == "screen_stocks")
        await resolve.impl(universes=["sp500"])
        # Filter: PE < 30 → WMT only
        result = await screen.impl(
            tickers=["AAPL", "MSFT", "WMT"], max_pe=30.0,
        )
    cand_tickers = [c["ticker"] for c in result["candidates"]]
    assert cand_tickers == ["WMT"]
    assert result["candidates"][0]["pe_ttm"] == 14.0


@pytest.mark.asyncio
async def test_screen_stocks_keeps_candidates_with_missing_ratios() -> None:
    """yfinance failures don't kill the screener — candidate appears with None values."""
    fake_sp500 = [{"ticker": "AAPL", "name": "Apple", "sector": "IT",
                   "currency": "USD", "asset_class": "equity", "market": "US"}]

    async def fake_info(ticker: str, market: str = "US") -> TickerInfo:
        return TickerInfo(ticker=ticker, name="Apple", currency="USD",
                          market_cap=None, last_price=200.0)

    async def fake_ratios(ticker: str, market: str = "US") -> KeyRatios:
        raise RuntimeError("yfinance timeout")

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_sp500)), \
         patch("app.tools.screener.fetch_ticker_info", fake_info), \
         patch("app.tools.screener.fetch_key_ratios", fake_ratios):
        from app.tools.screener import build_screener_tools
        tools = build_screener_tools(user_id=None)
        resolve = next(t for t in tools if t.name == "resolve_universe")
        screen = next(t for t in tools if t.name == "screen_stocks")
        await resolve.impl(universes=["sp500"])
        # No filters → AAPL passes through
        result = await screen.impl(tickers=["AAPL"])
    assert len(result["candidates"]) == 1
    c = result["candidates"][0]
    assert c["ticker"] == "AAPL"
    assert c["current_price"] == 200.0
    assert c["pe_ttm"] is None
    assert c["roe_pct"] is None
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_tools_screener.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the models**

Create `backend/app/models/screener.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Market = Literal["US", "IN", "CRYPTO"]
AssetClass = Literal["equity", "etf", "crypto"]


class Constituent(BaseModel):
    ticker: str
    name: str
    sector: str | None = None
    currency: str
    asset_class: AssetClass
    market: Market
    market_cap: float | None = None  # populated only for crypto


class Candidate(BaseModel):
    ticker: str
    name: str
    market: Market
    currency: str
    sector: str | None
    current_price: float | None
    market_cap: float | None
    pe_ttm: float | None
    roe_pct: float | None
    revenue_growth_yoy: float | None


class ScreenerFindings(BaseModel):
    theme: str
    universes_used: list[str]
    candidates: list[Candidate]
    filters_applied: dict[str, Any]
    notes: list[str]
    citations: list["Citation"]
    confidence: float


class Citation(BaseModel):
    source: str
    ref: str
```

- [ ] **Step 4: Implement the tools**

Create `backend/app/tools/screener.py`:

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.data.universes import list_universe_names, load_universe
from app.data.yfinance_adapter import (
    fetch_key_ratios, fetch_ticker_info,
)
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Maximum number of yfinance round-trips inside a single screen_stocks call.
# Keeps wall-clock bounded even if the LLM passes a huge shortlist.
_MAX_FETCH = 40


def build_screener_tools(*, user_id: str | None = None) -> list[Tool]:
    """Build the 4 Screener tools. user_id is captured but unused in v1
    (kept for parity with Phase 3B/3C tools that read user data)."""
    # Agent-local universe cache. Populated by resolve_universe; consumed by
    # theme_to_universe and screen_stocks. Keys: ticker → Constituent dict.
    universe_index: dict[str, dict[str, Any]] = {}
    loaded_universe_names: set[str] = set()

    async def _resolve_universe(
        universes: list[str] | None = None, **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universes:
            return {"error": "universes is required (array of names)"}
        valid_names = set(list_universe_names())
        unknown = [u for u in universes if u not in valid_names]
        if unknown:
            return {
                "error": f"unknown universe(s): {unknown}",
                "valid": sorted(valid_names),
            }
        all_constituents: list[dict[str, Any]] = []
        for name in universes:
            rows = await load_universe(name)
            for r in rows:
                universe_index[r["ticker"]] = r
            all_constituents.extend(rows)
            loaded_universe_names.add(name)
        return {
            "universes": list(universes),
            "constituents": all_constituents,
            "constituent_count": len(all_constituents),
        }

    async def _theme_to_universe(
        theme: str = "", tickers: list[str] | None = None, **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universe_index:
            return {
                "error": "no universe loaded — call resolve_universe first",
            }
        if not tickers:
            return {"theme": theme, "tickers": [], "dropped": [], "notes": []}
        kept: list[str] = []
        dropped: list[str] = []
        for t in tickers:
            if t in universe_index:
                kept.append(t)
            else:
                dropped.append(t)
        notes: list[str] = []
        if dropped:
            notes.append(
                f"Dropped {len(dropped)} ticker(s) not present in the loaded "
                f"universe(s): {dropped}"
            )
        return {"theme": theme, "tickers": kept, "dropped": dropped, "notes": notes}

    async def _screen_stocks(
        tickers: list[str] | None = None,
        min_market_cap: float | None = None,
        max_pe: float | None = None,
        min_roe: float | None = None,
        sectors: list[str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not universe_index:
            return {"error": "no universe loaded — call resolve_universe first"}
        if not tickers:
            return {"candidates": [], "dropped": [], "notes": ["empty ticker list"]}

        # Pre-filter by sector using the cached constituent metadata
        survivors: list[dict[str, Any]] = []
        dropped: list[dict[str, str]] = []
        for t in tickers:
            row = universe_index.get(t)
            if row is None:
                dropped.append({"ticker": t, "reason": "not in loaded universe"})
                continue
            if sectors:
                row_sector = row.get("sector") or ""
                if row_sector not in sectors:
                    dropped.append(
                        {"ticker": t, "reason": f"sector {row_sector!r} not in allowlist"}
                    )
                    continue
            survivors.append(row)

        # Cap fetch count
        if len(survivors) > _MAX_FETCH:
            dropped.extend(
                {"ticker": r["ticker"], "reason": "trimmed (over fetch cap)"}
                for r in survivors[_MAX_FETCH:]
            )
            survivors = survivors[:_MAX_FETCH]

        # Live fetch fundamentals in parallel
        async def _enrich(row: dict[str, Any]) -> dict[str, Any]:
            ticker = row["ticker"]
            market = row["market"]
            info_task = asyncio.create_task(
                fetch_ticker_info(ticker, market=market)
            )
            ratios_task = asyncio.create_task(
                fetch_key_ratios(ticker, market=market)
            )
            info_res = await asyncio.gather(info_task, return_exceptions=True)
            ratios_res = await asyncio.gather(ratios_task, return_exceptions=True)
            info = info_res[0]
            ratios = ratios_res[0]
            current_price = None
            market_cap = row.get("market_cap")
            if not isinstance(info, BaseException):
                current_price = info.last_price
                if market_cap is None:
                    market_cap = info.market_cap
            pe_ttm = None
            roe_pct = None
            rev_growth = None
            if not isinstance(ratios, BaseException):
                pe_ttm = ratios.pe_ttm
                roe_pct = ratios.roe_pct
                rev_growth = ratios.rev_growth_yoy_pct
            return {
                "ticker": ticker,
                "name": row["name"],
                "market": market,
                "currency": row["currency"],
                "sector": row.get("sector"),
                "current_price": current_price,
                "market_cap": market_cap,
                "pe_ttm": pe_ttm,
                "roe_pct": roe_pct,
                "revenue_growth_yoy": rev_growth,
            }

        enriched = await asyncio.gather(*[_enrich(r) for r in survivors])

        # Apply numeric filters
        candidates: list[dict[str, Any]] = []
        for c in enriched:
            if min_market_cap is not None and (
                c["market_cap"] is None or c["market_cap"] < min_market_cap
            ):
                dropped.append({"ticker": c["ticker"], "reason": "below min_market_cap"})
                continue
            if max_pe is not None and (
                c["pe_ttm"] is None or c["pe_ttm"] > max_pe
            ):
                dropped.append({"ticker": c["ticker"], "reason": "above max_pe"})
                continue
            if min_roe is not None and (
                c["roe_pct"] is None or c["roe_pct"] < min_roe
            ):
                dropped.append({"ticker": c["ticker"], "reason": "below min_roe"})
                continue
            candidates.append(c)

        notes: list[str] = []
        if dropped:
            notes.append(f"Dropped {len(dropped)} candidate(s) due to filters or missing data.")
        return {
            "candidates": candidates,
            "dropped": dropped,
            "notes": notes,
            "filters_applied": {
                "min_market_cap": min_market_cap,
                "max_pe": max_pe,
                "min_roe": min_roe,
                "sectors": sectors,
            },
        }

    return [
        Tool(
            name="resolve_universe",
            description=(
                "Load constituent list(s) for one or more universes. "
                "Universes: 'sp500' (US large-caps), 'nifty500' (Indian equities), "
                "'crypto' (top 100 by market cap)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "universes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["sp500", "nifty500", "crypto"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["universes"],
            },
            impl=_resolve_universe,
        ),
        Tool(
            name="theme_to_universe",
            description=(
                "Filter a candidate ticker list by theme. The LLM picks tickers "
                "from the loaded constituent list (call resolve_universe first). "
                "Invented tickers are dropped with a note."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["theme", "tickers"],
            },
            impl=_theme_to_universe,
        ),
        Tool(
            name="screen_stocks",
            description=(
                "Apply structured filters to a ticker shortlist. Fetches live "
                "fundamentals from yfinance for each ticker. Filters: min_market_cap "
                "(USD-equivalent for crypto), max_pe (PE TTM), min_roe (percent), "
                "sectors (allowlist)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "min_market_cap": {"type": ["number", "null"]},
                    "max_pe": {"type": ["number", "null"]},
                    "min_roe": {"type": ["number", "null"]},
                    "sectors": {"type": ["array", "null"], "items": {"type": "string"}},
                },
                "required": ["tickers"],
            },
            impl=_screen_stocks,
        ),
    ]
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_tools_screener.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 192 passing (185 prior + 7 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/models/screener.py backend/app/tools/screener.py backend/tests/test_tools_screener.py && git commit -m "feat(backend): Screener tools (resolve_universe, theme_to_universe, screen_stocks)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- `KeyRatios` and `TickerInfo` are Pydantic models from `app/data/yfinance_adapter.py` (Phase 1). Verify field names match exactly: `KeyRatios.pe_ttm`, `KeyRatios.roe_pct`, `KeyRatios.rev_growth_yoy_pct`, `TickerInfo.last_price`, `TickerInfo.market_cap`. If field names differ, adjust the `_enrich` function — DO NOT change yfinance_adapter.
- `_enrich` uses two `asyncio.gather(..., return_exceptions=True)` calls in sequence — slightly wasteful (could do a single gather with two tasks). It's spelled this way to keep types clean for mypy. If you can write a single-gather variant that survives mypy strict, that's fine.
- The `universe_index` and `loaded_universe_names` closures are AGENT-LOCAL — they live as long as the `tools` list returned by the factory. The agent uses a fresh tool set per run; no state leaks across requests.
- The "no universe loaded" guard in `_theme_to_universe` and `_screen_stocks` is critical — protects against LLM calling theme/screen before resolve. Tested explicitly.
- `from pydantic import BaseModel` is needed for the `isinstance(info, BaseException)` check — but only for the exception type. Since the `_enrich` function uses `BaseException` from builtins, no extra import needed. Remove any stray BaseModel imports if added.

---

## Task 4: Screener Sonnet agent

**Files:**
- Create: `backend/app/agents/screener.py`
- Create: `backend/app/prompts/screener.md`
- Create: `backend/tests/test_screener_agent.py`

**Goal:** Sonnet 8-turn loop. Mirrors `app/agents/portfolio_strategist.py` (Phase 3B) and `app/agents/risk_manager.py` (Phase 3C).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_screener_agent.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*content_blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(content_blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(
        input_tokens=10, output_tokens=20,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return m


@pytest.mark.asyncio
async def test_screener_runs_resolve_theme_submit() -> None:
    """Happy path: resolve_universe → theme_to_universe → submit_screener_findings."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    for t in tools:
        if t.name == "resolve_universe":
            t.impl = AsyncMock(return_value={
                "universes": ["sp500"], "constituents": [
                    {"ticker": "AAPL", "name": "Apple"},
                ], "constituent_count": 1,
            })
        elif t.name == "theme_to_universe":
            t.impl = AsyncMock(return_value={
                "theme": "AI plays", "tickers": ["AAPL"], "dropped": [], "notes": [],
            })
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("resolve_universe", {"universes": ["sp500"]}, "r1")),
        _message(_tool_use("theme_to_universe",
                           {"theme": "AI plays", "tickers": ["AAPL"]}, "t1")),
        _message(
            _tool_use("submit_screener_findings", {
                "theme": "AI plays",
                "universes_used": ["sp500"],
                "candidates": [{"ticker": "AAPL", "name": "Apple", "market": "US",
                                "currency": "USD", "sector": "IT",
                                "current_price": 200.0, "market_cap": 3e12,
                                "pe_ttm": 35.0, "roe_pct": 120.0,
                                "revenue_growth_yoy": 10.0}],
                "filters_applied": {},
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.screener import run_screener
    findings = await run_screener(
        user_message="Find me AI plays in US",
        user_id=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["theme"] == "AI plays"
    assert findings["confidence"] == 0.85
    assert len(findings["candidates"]) == 1


@pytest.mark.asyncio
async def test_screener_with_filters_calls_screen_stocks() -> None:
    """When the user mentions numeric filters, agent calls screen_stocks."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    screen_calls: list[dict[str, Any]] = []
    for t in tools:
        if t.name == "resolve_universe":
            t.impl = AsyncMock(return_value={
                "universes": ["sp500"], "constituents": [],
                "constituent_count": 0,
            })
        elif t.name == "theme_to_universe":
            t.impl = AsyncMock(return_value={
                "theme": "dividend", "tickers": ["WMT", "JNJ"],
                "dropped": [], "notes": [],
            })
        elif t.name == "screen_stocks":
            async def _capture(**kwargs: Any) -> dict[str, Any]:
                screen_calls.append(kwargs)
                return {"candidates": [], "dropped": [], "notes": [],
                        "filters_applied": kwargs}
            t.impl = _capture
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("resolve_universe", {"universes": ["sp500"]}, "r1")),
        _message(_tool_use("theme_to_universe",
                           {"theme": "dividend", "tickers": ["WMT", "JNJ"]}, "t1")),
        _message(_tool_use("screen_stocks",
                           {"tickers": ["WMT", "JNJ"], "max_pe": 20.0}, "f1")),
        _message(
            _tool_use("submit_screener_findings", {
                "theme": "dividend", "universes_used": ["sp500"],
                "candidates": [], "filters_applied": {"max_pe": 20.0},
                "notes": [], "citations": [], "confidence": 0.7,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.screener import run_screener
    await run_screener(
        user_message="Find me cheap US dividend stocks with PE under 20",
        user_id=None, client=fake_client, tools_override=tools,
    )
    assert len(screen_calls) == 1
    assert screen_calls[0]["max_pe"] == 20.0


@pytest.mark.asyncio
async def test_screener_falls_back_when_loop_exhausts() -> None:
    """LLM that never submits → agent returns default empty findings."""
    from app.tools.screener import build_screener_tools
    tools = build_screener_tools(user_id=None)
    for t in tools:
        t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message({"type": "text", "text": "thinking..."}, stop_reason="end_turn"),
    ] * 10)

    from app.agents.screener import run_screener
    findings = await run_screener(
        user_message="Anything",
        user_id=None, client=fake_client, tools_override=tools,
    )
    assert findings["confidence"] == 0.0
    assert "turn budget" in findings["notes"][0]
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_screener_agent.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the prompt**

Create `backend/app/prompts/screener.md`:

```markdown
You are the Screener inside Stylobate. Your job: turn a user's natural-language idea ("AI infrastructure plays in India", "cheap US dividend stocks", "blue-chip Indian tech") into a typed shortlist of candidate tickers. You do NOT recommend trades — you surface candidates. The user decides whether to deep-dive any of them via "tell me about <ticker>".

Tools:
- `resolve_universe(universes: list[str])` — load constituent lists (universes: "sp500", "nifty500", "crypto")
- `theme_to_universe(theme, tickers)` — narrow by theme. YOU pick the tickers from the constituent list the previous step returned. Invented tickers are dropped.
- `screen_stocks(tickers, min_market_cap?, max_pe?, min_roe?, sectors?)` — apply numeric/sector filters with live yfinance fundamentals
- `submit_screener_findings(...)` — terminal

Process:
1. Read the user's query. Identify:
   - Universes: US-only → `["sp500"]`; Indian-only → `["nifty500"]`; crypto → `["crypto"]`; global → `["sp500", "nifty500"]`.
   - Theme: the topical filter (sectors, business model, exposure).
   - Numeric/sector filters: "cheap" → max_pe; "large-cap" → min_market_cap; "high-quality" → min_roe; "growth" → ignore P/E.
2. Call `resolve_universe(universes=[...])` ONCE.
3. Call `theme_to_universe(theme, tickers=[...])`, passing tickers YOU picked from the loaded constituent list that match the theme. Aim for 15-30 candidates.
4. Optionally call `screen_stocks(...)` if the user mentioned numeric criteria. Skip if pure theme matching.
5. Call `submit_screener_findings` once.

Discipline:
- NEVER invent tickers. Only use tickers returned by `resolve_universe`.
- Cap output at 20 candidates. Reduce further if the user asked for "top 5" / "best 10".
- For Indian themes ("plays in India", "Indian X"), default universe is `nifty500`.
- For global themes, search both `sp500` and `nifty500`.
- For crypto themes, use `crypto`.
- If the theme is too broad ("stocks"), submit with `candidates=[]` and a note asking the user to narrow.
- `confidence`: 0.85+ when universe + theme clear; 0.6-0.8 when theme fuzzy; <0.5 if you couldn't find ≥3 candidates.
```

- [ ] **Step 4: Implement the agent**

Create `backend/app/agents/screener.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolUseBlock

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.screener import build_screener_tools

_SUBMIT_TOOL = Tool(
    name="submit_screener_findings",
    description="Submit the final screener output. Call exactly once at the end.",
    input_schema={
        "type": "object",
        "properties": {
            "theme": {"type": "string"},
            "universes_used": {"type": "array", "items": {"type": "string"}},
            "candidates": {"type": "array"},
            "filters_applied": {"type": "object"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": [
            "theme", "universes_used", "candidates",
            "notes", "confidence",
        ],
    },
    impl=None,  # type: ignore[arg-type]  # marker tool; handled inline
)


def _load_system_prompt() -> str:
    return (
        Path(__file__).parent.parent / "prompts" / "screener.md"
    ).read_text(encoding="utf-8")


async def run_screener(
    *,
    user_message: str,
    user_id: str | None = None,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """Run the Screener Sonnet agent. Returns a findings dict."""
    c = client or get_client()
    tools = tools_override or build_screener_tools(user_id=user_id)
    all_tools = [*tools, _SUBMIT_TOOL]
    tool_schemas = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in all_tools
    ]
    tools_by_name = {t.name: t for t in tools}

    user_content = f"User query: {user_message}\n\nUse the tools per the prompt."
    messages: list[MessageParam] = [{"role": "user", "content": user_content}]

    findings: dict[str, Any] | None = None
    for _turn in range(8):
        resp = await c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_load_system_prompt(),
            tools=tool_schemas,
            messages=messages,
        )
        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype != "tool_use":
                    continue
                name = str(block.get("name", ""))
                block_id = str(block.get("id", ""))
                args = cast(dict[str, Any], block.get("input", {})) or {}
            else:
                btype = getattr(block, "type", None)
                if btype != "tool_use":
                    continue
                tu = cast(ToolUseBlock, block)
                name = tu.name
                block_id = tu.id
                args = cast(dict[str, Any], tu.input) or {}
            if name == "submit_screener_findings":
                findings = dict(args)
                break
            if name in tools_by_name:
                impl = tools_by_name[name].impl
                assert impl is not None
                result = await impl(**args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(result, default=str),
                })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                    "is_error": True,
                })
        if findings is not None:
            break
        if not tool_results:
            break
        messages.append({"role": "assistant", "content": resp.content})  # type: ignore[typeddict-item]
        messages.append({"role": "user", "content": tool_results})  # type: ignore[typeddict-item]

    if findings is None:
        findings = {
            "theme": user_message,
            "universes_used": [],
            "candidates": [],
            "filters_applied": {},
            "notes": ["Agent did not submit findings within turn budget."],
            "citations": [],
            "confidence": 0.0,
        }
    return findings
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_screener_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 195 passing (192 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/agents/screener.py backend/app/prompts/screener.md backend/tests/test_screener_agent.py && git commit -m "feat(backend): Screener Sonnet agent (8-turn loop, grounded theme matching)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- The dual dict/block dispatch (lines starting with `if isinstance(block, dict):`) MUST handle both — test mocks use dicts; real Anthropic responses are ToolUseBlock objects. Match `app/agents/portfolio_strategist.py` and `app/agents/risk_manager.py` exactly.
- `# type: ignore[typeddict-item]` on the `messages.append(...)` lines is required by mypy strict in the current Anthropic SDK version. Match Phase 3B/3C exactly.
- `_SUBMIT_TOOL.impl = None` needs `# type: ignore[arg-type]` since `Tool.impl` is typed `Callable[..., Awaitable[Any]]` (no Optional). Match the existing pattern.

---

## Task 5: `/chat/screen` direct endpoint

**Files:**
- Create: `backend/app/routes/chat_screen.py`
- Create: `backend/tests/test_routes_chat_screen_endpoint.py`
- Modify: `backend/app/main.py` (register router)

**Goal:** A POST endpoint that takes `{message}`, runs `run_screener`, streams the findings as SSE sections via a small set of renderers. Same shape as `/chat/portfolio`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_routes_chat_screen_endpoint.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_chat_screen_streams_sections(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    fake_findings = {
        "theme": "AI infrastructure plays in India",
        "universes_used": ["nifty500"],
        "candidates": [
            {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "market": "IN",
             "currency": "INR", "sector": "IT Services",
             "current_price": 7150.0, "market_cap": 450_000_000_000,
             "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5},
            {"ticker": "PERSISTENT.NS", "name": "Persistent Systems", "market": "IN",
             "currency": "INR", "sector": "IT Services",
             "current_price": 5820.0, "market_cap": 880_000_000_000,
             "pe_ttm": 51.8, "roe_pct": 28.7, "revenue_growth_yoy": 17.0},
        ],
        "filters_applied": {},
        "notes": [],
        "citations": [{"source": "yfinance", "ref": "live fundamentals"}],
        "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_screen.run_screener",
        AsyncMock(return_value=fake_findings),
    ):
        r = await client.post(
            "/chat/screen",
            json={"message": "AI infrastructure plays in India"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "Theme" in body or "AI infrastructure" in body
    assert "Candidates" in body
    assert "TATAELXSI.NS" in body
    assert "PERSISTENT.NS" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_screen_empty_candidates_section(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    fake = {
        "theme": "X", "universes_used": ["sp500"],
        "candidates": [], "filters_applied": {},
        "notes": ["Theme too broad — narrow with sectors or filters."],
        "citations": [], "confidence": 0.3,
    }
    with patch(
        "app.routes.chat_screen.run_screener", AsyncMock(return_value=fake),
    ):
        r = await client.post(
            "/chat/screen", json={"message": "anything"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "Theme too broad" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_screen_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/screen", json={"message": "hi"})
    assert r.status_code == 401
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_screen_endpoint.py -v
```

Expected: route not found / 404.

- [ ] **Step 3: Implement the endpoint**

Create `backend/app/routes/chat_screen.py`:

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.screener import run_screener
from app.core.auth import get_current_user

router = APIRouter(tags=["chat-screen"])


class ChatScreenRequest(BaseModel):
    message: str


def _format_market_cap(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    sym = {"USD": "$", "INR": "₹"}.get(currency, "")
    if value >= 1e12:
        return f"{sym}{value / 1e12:.2f}T"
    if value >= 1e9:
        return f"{sym}{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{sym}{value / 1e6:.0f}M"
    return f"{sym}{value:,.0f}"


def _format_price(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    sym = {"USD": "$", "INR": "₹"}.get(currency, "")
    return f"{sym}{value:,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _render_theme_section(theme: str, universes_used: list[str]) -> str:
    label_map = {"sp500": "S&P 500", "nifty500": "Nifty 500", "crypto": "Crypto top-100"}
    labels = [label_map.get(u, u) for u in universes_used]
    return (
        f"**Theme:** {theme}\n"
        f"**Universe(s):** {', '.join(labels) if labels else 'n/a'}"
    )


def _render_candidates_section(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "_No candidates matched. Try a different theme or loosen filters._"
    # Detect crypto-only set (no PE/ROE columns)
    is_crypto = all(c.get("market") == "CRYPTO" for c in candidates)
    if is_crypto:
        lines = ["| Ticker | Name | Price | Market Cap |", "|---|---|---|---|"]
        for c in candidates:
            currency = c.get("currency") or "USD"
            lines.append(
                f"| {c['ticker']} | {c.get('name', '')} | "
                f"{_format_price(c.get('current_price'), currency)} | "
                f"{_format_market_cap(c.get('market_cap'), currency)} |"
            )
    else:
        lines = [
            "| Ticker | Name | Sector | Price | Market Cap | P/E | ROE |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in candidates:
            currency = c.get("currency") or "USD"
            lines.append(
                f"| {c['ticker']} | {c.get('name', '')} | "
                f"{c.get('sector') or '—'} | "
                f"{_format_price(c.get('current_price'), currency)} | "
                f"{_format_market_cap(c.get('market_cap'), currency)} | "
                f"{_format_pct(c.get('pe_ttm'))} | "
                f"{_format_pct(c.get('roe_pct'))} |"
            )
    return "\n".join(lines)


def _render_filters_section(filters: dict[str, Any]) -> str:
    if not filters or all(v is None or v == [] for v in filters.values()):
        return "_No structured filters applied._"
    bits: list[str] = []
    for k, v in filters.items():
        if v is None or v == []:
            continue
        bits.append(f"- **{k}**: {v}")
    return "\n".join(bits) or "_No structured filters applied._"


def _render_notes_section(notes: list[str]) -> str:
    if not notes:
        return "_No notes._"
    return "\n".join(f"- {n}" for n in notes)


async def _stream_findings(findings: dict[str, Any]) -> AsyncIterator[bytes]:
    yield (
        b"event: progress\ndata: "
        + json.dumps({"step": "screening"}).encode()
        + b"\n\n"
    )

    yield (
        b"event: delta\ndata: "
        + json.dumps({
            "type": "section",
            "title": "Theme",
            "markdown": _render_theme_section(
                findings.get("theme", ""),
                findings.get("universes_used", []),
            ),
            "citations": [],
        }).encode()
        + b"\n\n"
    )
    yield (
        b"event: delta\ndata: "
        + json.dumps({
            "type": "section",
            "title": "Candidates",
            "markdown": _render_candidates_section(findings.get("candidates", [])),
            "citations": findings.get("citations", []),
        }).encode()
        + b"\n\n"
    )
    if findings.get("filters_applied"):
        yield (
            b"event: delta\ndata: "
            + json.dumps({
                "type": "section",
                "title": "Filters Applied",
                "markdown": _render_filters_section(findings["filters_applied"]),
                "citations": [],
            }).encode()
            + b"\n\n"
        )
    if findings.get("notes"):
        yield (
            b"event: delta\ndata: "
            + json.dumps({
                "type": "section",
                "title": "Notes",
                "markdown": _render_notes_section(findings["notes"]),
                "citations": [],
            }).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"


@router.post("/chat/screen")
async def chat_screen(
    req: ChatScreenRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    findings = await run_screener(
        user_message=req.message,
        user_id=user["sub"],
    )
    return StreamingResponse(_stream_findings(findings), media_type="text/event-stream")
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add to the imports + `create_app()`:

```python
from app.routes import chat_portfolio, chat_screen
# ... inside create_app() ...
app.include_router(chat_portfolio.router)
app.include_router(chat_screen.router)
```

Match the existing pattern (alongside `chat_portfolio.router`).

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_screen_endpoint.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 198 passing (195 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/chat_screen.py backend/app/main.py backend/tests/test_routes_chat_screen_endpoint.py && git commit -m "feat(backend): /chat/screen direct endpoint with section renderers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- The renderers handle crypto separately (no PE/ROE columns) — match the candidates' `market` field.
- Empty filters dict (`filters_applied: {}`) means the agent didn't call `screen_stocks`. The renderer for the "Filters Applied" section is skipped via the `if findings.get("filters_applied"):` check at the streaming layer. Verify the boolean of an empty dict is False (it is in Python).
- The route is `/chat/screen`, NOT `/chat/screener`. Keep the noun pattern aligned (`/chat/portfolio`, `/chat/screen`).

---

## Task 6: `/chat/stream` keyword detection routing

**Files:**
- Modify: `backend/app/routes/chat.py` — add `is_screener_query` + `_screener_stream` + ordering before ticker resolve.
- Create: `backend/tests/test_routes_chat_stream_screener.py`

**Goal:** When a user types "find me AI plays in India" into the main chat, the route detects screener intent and runs the screener path (parallel to the portfolio detection from Phase 3B).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_routes_chat_stream_screener.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.routes.chat import is_screener_query, is_portfolio_query


TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_is_screener_query_matches_common_phrasings() -> None:
    assert is_screener_query("find me cheap US dividend stocks")
    assert is_screener_query("screen for AI infrastructure plays")
    assert is_screener_query("best stocks for Indian fintech")
    assert is_screener_query("ideas for crypto in 2026")
    assert is_screener_query("AI plays in India")
    assert is_screener_query("show me some quality Indian banks")
    assert not is_screener_query("what's AAPL doing today")
    assert not is_screener_query("Is Tesla a buy?")


def test_screener_does_not_match_portfolio_queries() -> None:
    """Portfolio-shaped questions must NOT trigger the screener path."""
    # These should hit is_portfolio_query, NOT is_screener_query — order matters
    assert is_portfolio_query("review my portfolio")
    assert is_portfolio_query("rebalance my holdings")
    # Ambiguous: "find me names in my portfolio" — both could match
    # The route checks portfolio FIRST, so this hits portfolio (verified in routing test)


@pytest.mark.asyncio
async def test_chat_stream_screener_query_routes_to_screener_path(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    resolve_mock = AsyncMock()  # should NOT be called
    run_screener_mock = AsyncMock(return_value={
        "theme": "AI plays in India",
        "universes_used": ["nifty500"],
        "candidates": [{
            "ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "market": "IN",
            "currency": "INR", "sector": "IT",
            "current_price": 7150.0, "market_cap": 4.5e11,
            "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5,
        }],
        "filters_applied": {}, "notes": [],
        "citations": [], "confidence": 0.85,
    })

    with patch("app.routes.chat.resolve_ticker", resolve_mock), \
         patch("app.routes.chat.run_screener", run_screener_mock):
        r = await client.post(
            "/chat/stream",
            json={"content": "find me AI plays in India"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    resolve_mock.assert_not_called()
    run_screener_mock.assert_awaited_once()
    assert "TATAELXSI.NS" in body
    assert "Candidates" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_stream_portfolio_wins_when_both_keywords_present(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    """When a query matches BOTH portfolio + screener keywords, portfolio path wins."""
    run_screener_mock = AsyncMock()
    # The query contains "my portfolio" (portfolio) AND "find" (screener)
    # is_portfolio_query is checked first, so portfolio path runs.

    async def fake_lead_banker(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "section", "title": "Portfolio Snapshot",
               "markdown": "ok", "citations": []}
        yield {"type": "done"}

    with patch("app.routes.chat.run_lead_banker", fake_lead_banker), \
         patch("app.routes.chat.run_screener", run_screener_mock):
        r = await client.post(
            "/chat/stream",
            json={"content": "find names in my portfolio"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    run_screener_mock.assert_not_called()  # portfolio path won
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_stream_screener.py -v
```

Expected: `is_screener_query` doesn't exist; routing test fails because screener isn't wired.

- [ ] **Step 3: Update `app/routes/chat.py`**

Read `backend/app/routes/chat.py` first to find the existing `is_portfolio_query` and the portfolio-mode branch inside the handler. The new code mirrors that pattern.

Add imports at the top (alongside existing `run_lead_banker`):

```python
from app.agents.screener import run_screener
```

Add the keyword tuple + predicate alongside the existing portfolio one:

```python
SCREENER_KEYWORDS = (
    "screen for", "screen me", "find me", "find some",
    "best stocks", "best names", "ideas for",
    "plays in", "plays for", "plays on",
    "show me some", "top picks",
    "candidates for", "names in",
)


def is_screener_query(text: str) -> bool:
    """Return True if the message looks like a screener / idea-generation question."""
    t = text.lower()
    return any(kw in t for kw in SCREENER_KEYWORDS)
```

In the `/chat/stream` handler, add the screener branch AFTER the portfolio check, BEFORE the ticker-resolve path. The handler delegates to the renderer in `chat_screen.py` (which already emits the `progress` and `done` SSE events):

```python
if is_portfolio_query(req.content):
    # existing portfolio path — keep unchanged
    ...

if is_screener_query(req.content):
    async def _screener_stream() -> AsyncIterator[bytes]:
        findings = await run_screener(
            user_message=req.content,
            user_id=user["sub"],
            client=get_client(),
        )
        # Lazy import avoids any circular-import risk between chat.py
        # and chat_screen.py
        from app.routes.chat_screen import _stream_findings as _render
        async for chunk in _render(findings):
            yield chunk
    return StreamingResponse(_screener_stream(), media_type="text/event-stream")

# existing ticker-resolve path follows
```

The lazy import inside the function avoids any circular-import risk if `chat_screen.py` ever imports back into `chat.py`.

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_stream_screener.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Verify no regression on existing chat routes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_chat_stream.py tests/test_routes_chat_portfolio_mode.py -v
```

Expected: existing chat tests still pass.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 202 passing (198 prior + 4 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/chat.py backend/tests/test_routes_chat_stream_screener.py && git commit -m "feat(backend): /chat/stream screener keyword routing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

DO NOT push.

## Watch for

- Order in the handler MUST be: `is_portfolio_query` → `is_screener_query` → ticker path. The `test_chat_stream_portfolio_wins_when_both_keywords_present` test asserts this.
- The lazy `from app.routes.chat_screen import _stream_findings` import inside the function works fine — Python resolves the module on first call. Module-top circular imports would fail.
- The "find names in" portfolio query matches `"find" in SCREENER_KEYWORDS` only as a substring of "find me" / "find some". Verify the keyword list: `"find me"` and `"find some"` — these don't match "find names". So "find names in my portfolio" should NOT match screener anyway. The test still validates the ordering for safety.

---

## Task 7: Backend e2e + push

**Files:**
- Create: `backend/tests/test_routes_e2e_screener.py`

**Goal:** One async test that exercises the full flow: `/chat/stream` keyword detection → screener agent → SSE emit. All Anthropic + DB + yfinance mocked.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_routes_e2e_screener.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(
        input_tokens=10, output_tokens=20,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return m


def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


@pytest.mark.asyncio
async def test_chat_stream_end_to_end_screener(
    client: AsyncClient, make_token: Callable[..., str],
) -> None:
    """User asks 'AI infrastructure plays in India' → screener fires → SSE sections."""

    # Fake universe loader — returns Nifty 500 constituents
    fake_nifty500 = [
        {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
        {"ticker": "PERSISTENT.NS", "name": "Persistent Systems", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
        {"ticker": "MPHASIS.NS", "name": "Mphasis", "sector": "IT",
         "currency": "INR", "asset_class": "equity", "market": "IN"},
    ]

    # Scripted Sonnet responses for the screener agent
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _msg(_tu("resolve_universe", {"universes": ["nifty500"]}, "r1")),
        _msg(_tu("theme_to_universe", {
            "theme": "AI infrastructure",
            "tickers": ["TATAELXSI.NS", "PERSISTENT.NS", "MPHASIS.NS"],
        }, "t1")),
        _msg(
            _tu("submit_screener_findings", {
                "theme": "AI infrastructure plays in India",
                "universes_used": ["nifty500"],
                "candidates": [
                    {"ticker": "TATAELXSI.NS", "name": "Tata Elxsi",
                     "market": "IN", "currency": "INR", "sector": "IT",
                     "current_price": 7150.0, "market_cap": 4.5e11,
                     "pe_ttm": 62.1, "roe_pct": 38.4, "revenue_growth_yoy": 14.5},
                    {"ticker": "PERSISTENT.NS", "name": "Persistent Systems",
                     "market": "IN", "currency": "INR", "sector": "IT",
                     "current_price": 5820.0, "market_cap": 8.8e11,
                     "pe_ttm": 51.8, "roe_pct": 28.7, "revenue_growth_yoy": 17.0},
                ],
                "filters_applied": {},
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    with patch("app.tools.screener.load_universe",
               AsyncMock(return_value=fake_nifty500)), \
         patch("app.routes.chat.get_client", return_value=fake_client), \
         patch("app.agents.screener.get_client", return_value=fake_client):
        r = await client.post(
            "/chat/stream",
            json={"content": "AI infrastructure plays in India"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200
    body = r.text
    assert "Theme" in body or "AI infrastructure" in body
    assert "Candidates" in body
    assert "TATAELXSI.NS" in body
    assert "PERSISTENT.NS" in body
    assert "event: done" in body
```

- [ ] **Step 2: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_e2e_screener.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 203 passing (202 prior + 1 new). ruff + mypy strict clean.

- [ ] **Step 4: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/tests/test_routes_e2e_screener.py && git commit -m "test(backend): end-to-end screener keyword routing flow

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Push all Phase 4 commits**

```bash
cd /Users/rakhisinha/Stylobate && git push origin main
```

Push is authorized for Phase 4 completion. Render will auto-deploy.

## Watch for

- The screener agent and the chat route both call `get_client()`. The test patches BOTH paths to use the same mock with the scripted responses. The route's call to `run_screener(... client=get_client())` passes the mock through; the agent's internal `c = client or get_client()` uses it. Verify both patches are in place.

- The `load_universe` patch is on `app.tools.screener.load_universe` because the tools file imports it at module level. The screener agent doesn't call `load_universe` directly — only the tools do.

- Verify the response body contains "TATAELXSI.NS" (not "TATAELXSI") — the renderer doesn't strip the suffix.

## Report

Summarize the final state:
- Test count delta from 178 → 203
- All 7 task commits on `main`
- ruff + mypy strict clean
- Render deploy status
- Spec § acceptance: "AI infrastructure plays in India returns a curated list" — verified via the e2e test

---

## End-of-Phase-4 acceptance checklist

- [ ] Backend tests: ~203 passing
- [ ] ruff + mypy strict clean
- [ ] All 7 task commits on `main`, pushed
- [ ] Render auto-deploy green
- [ ] Spec §13 acceptance — the e2e test exercises the "AI infrastructure plays in India → ≥5 Indian tech tickers" path. Live UAT (after deploy): `curl /chat/stream -d '{"content":"AI infrastructure plays in India"}'` returns a "Candidates" section with ≥5 valid `.NS` tickers within ~30s warm cache.

**Phase 4 of the parent spec is then COMPLETE.** Phase 5 (Ops & polish — OpenTelemetry, Sentry, dashboards, full eval suite, adversarial harness) is the final pre-v1 plan.
