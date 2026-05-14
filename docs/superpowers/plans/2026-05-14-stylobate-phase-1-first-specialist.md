# Stylobate — Phase 1 First Specialist End-to-End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 0 echo with a real multi-agent flow for one specialist. End state: a user types "deep dive AAPL" and gets a streamed research note with citations to SEC filings + yfinance data, plus a recommendation card.

**Architecture:** Three agents wired together. Ticker Resolver (Haiku) maps user text → typed ticker. Lead Banker (Opus) plans + synthesizes. Fundamental Analyst (Sonnet) runs in parallel against the user's request with three tools (`get_financials`, `get_key_ratios`, `get_filings`). Lead Banker emits structured deltas (`progress`, `stock_card`, `section`, `recommendation`, `disclaimer`, `done`) via "emit" tools — each tool call streams a single SSE frame to the frontend. Prompt caching marks each agent's system prompt + tool defs as ephemeral so multi-call queries reuse them.

**Tech Stack:** Existing Phase 0 stack + `anthropic` SDK (already installed) for LLM calls, `yfinance` (new) for prices/financials, direct `httpx` calls for SEC EDGAR (free, official, no extra lib needed beyond a small ticker→CIK lookup). `pandas` (already installed) for shaping financials. Tests use `respx` for HTTP mocking and inline-fixture `anthropic` mocks.

**Spec reference:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` — implements §4.1 (Lead Banker), §4.2 (Fundamental Analyst), §4.4 (Ticker Resolver), §5.1 (yfinance, EDGAR data sources), §5.2 (`get_financials`, `get_filings`, `get_key_ratios` tool function signatures), §6.2 steps 3–8 (request flow), §10 (citation discipline + disclaimer), Appendix A (prompt-cache layout).

**Out of scope here (later phases):**
- Other specialists: Technical, News & Sentiment, Macro, Portfolio, Risk, Screener (Phases 2–4)
- Indian data sources, screener.in, BSE/NSE, MCA21, RBI, Damodaran (Phase 2)
- DCF runner (`run_dcf`) and peer comparables (`get_comparables`) — deferred to Phase 2
- Tool-result caching (`cache_kv` table population) — Phase 2; in-memory LRU only for now
- Portfolio CRUD, watchlists, screener (Phases 3–4)
- OpenTelemetry, Sentry (Phase 5)

---

## File map for Phase 1

```
stylobate/
├── backend/
│   ├── pyproject.toml                         MODIFY (add yfinance, pandas, tenacity)
│   ├── app/
│   │   ├── core/
│   │   │   ├── anthropic_client.py            CREATE
│   │   │   ├── output_validator.py            CREATE
│   │   │   └── (existing files unchanged)
│   │   ├── data/
│   │   │   ├── __init__.py                    CREATE (empty)
│   │   │   ├── yfinance_adapter.py            CREATE
│   │   │   └── edgar.py                       CREATE
│   │   ├── tools/
│   │   │   ├── __init__.py                    CREATE (empty)
│   │   │   ├── base.py                        CREATE  (typed Tool wrapper)
│   │   │   ├── financials.py                  CREATE  (get_financials, get_key_ratios)
│   │   │   └── filings.py                     CREATE  (get_filings)
│   │   ├── agents/
│   │   │   ├── __init__.py                    CREATE (empty)
│   │   │   ├── ticker_resolver.py             CREATE
│   │   │   ├── fundamental.py                 CREATE
│   │   │   └── lead_banker.py                 CREATE
│   │   ├── prompts/
│   │   │   ├── ticker_resolver.md             CREATE
│   │   │   ├── fundamental.md                 CREATE
│   │   │   └── lead_banker.md                 CREATE
│   │   ├── routes/
│   │   │   └── chat.py                        MODIFY (replace echo with agent flow)
│   │   └── db/
│   │       └── (existing files unchanged)
│   └── tests/
│       ├── test_anthropic_client.py           CREATE
│       ├── test_yfinance_adapter.py           CREATE
│       ├── test_edgar.py                      CREATE
│       ├── test_tools_financials.py           CREATE
│       ├── test_tools_filings.py              CREATE
│       ├── test_ticker_resolver.py            CREATE
│       ├── test_fundamental_agent.py          CREATE
│       ├── test_lead_banker_agent.py          CREATE
│       ├── test_output_validator.py           CREATE
│       └── test_chat_stream.py                MODIFY
│
└── frontend/
    ├── lib/
    │   └── types.ts                            MODIFY (new delta types)
    ├── components/
    │   ├── stock-card.tsx                      CREATE
    │   ├── recommendation-card.tsx             CREATE
    │   ├── section-block.tsx                   CREATE
    │   ├── citation-badge.tsx                  CREATE
    │   ├── progress-strip.tsx                  CREATE
    │   ├── disclaimer.tsx                      CREATE
    │   └── chat-thread.tsx                     MODIFY (switch on delta type)
    ├── app/chat/page.tsx                       MODIFY (handle new delta types in handleSend)
    └── __tests__/
        └── delta-renderers.test.tsx            CREATE
```

---

## Glossary of types used across tasks

These pydantic models are defined in Task 4 and reused everywhere downstream. If you read tasks out of order, refer back here.

```python
# backend/app/tools/financials.py — Task 4
class Financials(BaseModel):
    ticker: str
    period: str             # e.g. "FY2024", "Q3 FY2025"
    revenue: float | None
    net_income: float | None
    operating_cash_flow: float | None
    free_cash_flow: float | None
    currency: str

class KeyRatios(BaseModel):
    ticker: str
    as_of: date
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    roe: float | None
    roic: float | None
    fcf_yield: float | None
    debt_to_equity: float | None
    current_ratio: float | None
    net_margin: float | None
    revenue_growth_yoy: float | None
```

```python
# backend/app/tools/filings.py — Task 4
class FilingRef(BaseModel):
    type: str               # "10-K", "10-Q", "8-K", "annual_report", "quarterly"
    date: date
    url: str
    accession: str | None   # SEC accession number when known
    summary: str | None     # one-line summary if available
```

```python
# backend/app/agents/ticker_resolver.py — Task 5
class TickerResolution(BaseModel):
    ticker: str             # canonical, with market suffix (e.g. "AAPL", "RELIANCE.NS")
    name: str               # e.g. "Apple Inc."
    market: Literal["US", "IN", "CRYPTO"]
    asset_class: Literal["equity", "etf", "crypto"]
    confidence: float       # [0.0, 1.0]
    candidates: list[str] = []  # if ambiguous, alt tickers
```

```python
# backend/app/agents/fundamental.py — Task 6
class Citation(BaseModel):
    source: str             # "yfinance" | "edgar" | "alpha_vantage" etc.
    ref: str                # URL or e.g. "AAPL 10-K 2024" or "yfinance:financials:FY2024"
    snippet: str | None = None

class FundamentalFindings(BaseModel):
    ticker: str
    thesis: str             # 2-4 sentence summary
    fundamentals_summary: list[str]   # bullet points
    risks: list[str]                  # bullet points
    citations: list[Citation]
    confidence: float
```

```python
# backend/app/core/output_validator.py — Task 8
class ValidationError(Exception): ...
class ValidationResult(BaseModel):
    ok: bool
    issues: list[str]
```

---

## Task 1: Anthropic client wrapper with prompt caching

**Files:**
- Create: `backend/app/core/anthropic_client.py`
- Create: `backend/tests/test_anthropic_client.py`

**Goal:** A tiny wrapper around `anthropic.AsyncAnthropic` that builds a system-prompt block list with `cache_control` markers and exposes one helper for "give me a Messages response with caching baked in". The wrapper is the only place that imports `anthropic` directly; agents call into it.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_anthropic_client.py`:

```python
# backend/tests/test_anthropic_client.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_call_with_cache_sends_cache_control_blocks() -> None:
    from app.core.anthropic_client import call_with_cache

    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(type="text", text="hi")]
    fake_resp.stop_reason = "end_turn"
    fake_resp.usage = MagicMock(
        input_tokens=10, output_tokens=2,
        cache_creation_input_tokens=8, cache_read_input_tokens=0,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_resp)

    result = await call_with_cache(
        client=fake_client,
        model="claude-haiku-4-5",
        system_blocks=[
            {"text": "You are a helper."},
            {"text": "Tool defs go here."},
        ],
        tools=[],
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
    )

    assert result.text == "hi"
    call_args = fake_client.messages.create.call_args.kwargs
    assert call_args["model"] == "claude-haiku-4-5"
    assert call_args["max_tokens"] == 100
    sys_blocks = call_args["system"]
    assert len(sys_blocks) == 2
    # Every cached system block must carry cache_control: ephemeral
    for block in sys_blocks:
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_call_with_cache_returns_usage() -> None:
    from app.core.anthropic_client import call_with_cache

    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(type="text", text="ok")]
    fake_resp.stop_reason = "end_turn"
    fake_resp.usage = MagicMock(
        input_tokens=100, output_tokens=10,
        cache_creation_input_tokens=80, cache_read_input_tokens=20,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_resp)

    result = await call_with_cache(
        client=fake_client,
        model="claude-sonnet-4-6",
        system_blocks=[{"text": "sys"}],
        tools=[],
        messages=[{"role": "user", "content": "go"}],
        max_tokens=10,
    )
    assert result.usage.input_tokens == 100
    assert result.usage.cache_read_input_tokens == 20
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_anthropic_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.core.anthropic_client'`.

- [ ] **Step 3: Create `backend/app/core/anthropic_client.py`**

```python
# backend/app/core/anthropic_client.py
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from app.config import get_settings


@lru_cache(maxsize=1)
def get_client() -> AsyncAnthropic:
    settings = get_settings()
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


@dataclass
class AgentReply:
    text: str
    raw: Message
    usage: Any        # anthropic Usage object
    stop_reason: str | None


async def call_with_cache(
    *,
    client: AsyncAnthropic | Any,
    model: str,
    system_blocks: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float = 0.3,
) -> AgentReply:
    """Call Anthropic Messages with the supplied system blocks marked for caching.

    `system_blocks` should be in the order [persona, tool_defs_summary, guidelines].
    Each block becomes `{"type": "text", "text": ..., "cache_control": ephemeral}`.
    """
    sys = [
        {"type": "text", "text": b["text"], "cache_control": {"type": "ephemeral"}}
        for b in system_blocks
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": sys,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools

    resp = await client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return AgentReply(text=text, raw=resp, usage=resp.usage, stop_reason=resp.stop_reason)
```

- [ ] **Step 4: Run the tests, confirm they pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_anthropic_client.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Full backend suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: ruff clean, mypy clean, all tests pass (10 from Phase 0 + 2 new = 12).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/core/anthropic_client.py backend/tests/test_anthropic_client.py
git commit -m "feat(backend): add Anthropic client wrapper with prompt cache

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: yfinance data adapter

**Files:**
- Modify: `backend/pyproject.toml` (add `yfinance` and `tenacity` deps)
- Create: `backend/app/data/__init__.py` (empty)
- Create: `backend/app/data/yfinance_adapter.py`
- Create: `backend/tests/test_yfinance_adapter.py`

**Goal:** A thin adapter over the `yfinance` library returning typed records. yfinance does sync HTTP calls; we wrap them in `asyncio.to_thread` so they don't block the event loop. Tests mock the `yfinance.Ticker` factory directly — no real network.

- [ ] **Step 1: Add dependencies**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv add yfinance tenacity pandas
```

Verify `yfinance`, `tenacity`, and `pandas` are now in `pyproject.toml` `[project] dependencies`.

- [ ] **Step 2: Create `backend/app/data/__init__.py`** (empty)

```bash
touch backend/app/data/__init__.py
```

- [ ] **Step 3: Write the failing test**

`backend/tests/test_yfinance_adapter.py`:

```python
# backend/tests/test_yfinance_adapter.py
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def fake_ticker() -> MagicMock:
    mock = MagicMock()
    mock.info = {
        "shortName": "Apple Inc.",
        "longName": "Apple Inc.",
        "currency": "USD",
        "marketCap": 3_500_000_000_000,
        "trailingPE": 29.5,
        "priceToBook": 50.1,
        "priceToSalesTrailing12Months": 8.8,
        "returnOnEquity": 1.45,
        "debtToEquity": 152.0,
        "currentRatio": 0.98,
        "profitMargins": 0.255,
        "revenueGrowth": 0.062,
    }
    mock.income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2024-09-28"): {
                "Total Revenue": 391_035_000_000,
                "Net Income": 93_736_000_000,
            },
            pd.Timestamp("2023-09-30"): {
                "Total Revenue": 383_285_000_000,
                "Net Income": 96_995_000_000,
            },
        }
    )
    mock.cashflow = pd.DataFrame(
        {
            pd.Timestamp("2024-09-28"): {
                "Operating Cash Flow": 118_254_000_000,
                "Free Cash Flow": 108_807_000_000,
            },
            pd.Timestamp("2023-09-30"): {
                "Operating Cash Flow": 110_543_000_000,
                "Free Cash Flow": 99_584_000_000,
            },
        }
    )
    mock.fast_info = MagicMock(last_price=225.30, market_cap=3_500_000_000_000)
    return mock


@pytest.mark.asyncio
async def test_fetch_ticker_info_maps_yfinance_fields(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_ticker_info
        info = await fetch_ticker_info("AAPL")
    assert info.ticker == "AAPL"
    assert info.name == "Apple Inc."
    assert info.currency == "USD"
    assert info.market_cap == 3_500_000_000_000


@pytest.mark.asyncio
async def test_fetch_financials_returns_periods_in_order(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_financials
        results = await fetch_financials("AAPL", periods=2)
    assert [r.period for r in results] == ["FY2024", "FY2023"]
    assert results[0].revenue == 391_035_000_000
    assert results[0].net_income == 93_736_000_000
    assert results[0].operating_cash_flow == 118_254_000_000
    assert results[0].free_cash_flow == 108_807_000_000
    assert results[0].currency == "USD"


@pytest.mark.asyncio
async def test_fetch_key_ratios_picks_correct_fields(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_key_ratios
        ratios = await fetch_key_ratios("AAPL")
    assert ratios.pe_ttm == 29.5
    assert ratios.pb == 50.1
    assert ratios.roe == 1.45
    assert ratios.net_margin == 0.255
    assert ratios.revenue_growth_yoy == 0.062


@pytest.mark.asyncio
async def test_fetch_returns_none_for_missing_fields() -> None:
    bare = MagicMock()
    bare.info = {"shortName": "X", "currency": "USD"}
    bare.income_stmt = pd.DataFrame()
    bare.cashflow = pd.DataFrame()
    bare.fast_info = MagicMock(last_price=None, market_cap=None)
    with patch("app.data.yfinance_adapter._make_ticker", return_value=bare):
        from app.data.yfinance_adapter import fetch_key_ratios
        r = await fetch_key_ratios("X")
    assert r.pe_ttm is None
    assert r.net_margin is None
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.yfinance_adapter'`.

- [ ] **Step 5: Create `backend/app/data/yfinance_adapter.py`**

```python
# backend/app/data/yfinance_adapter.py
import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

import yfinance as yf  # type: ignore[import-untyped]
from pydantic import BaseModel


def _make_ticker(ticker: str) -> Any:
    """Indirection so tests can patch this exact name."""
    return yf.Ticker(ticker)


class TickerInfo(BaseModel):
    ticker: str
    name: str
    currency: str
    market_cap: int | None
    last_price: float | None


class Financials(BaseModel):
    ticker: str
    period: str
    revenue: float | None
    net_income: float | None
    operating_cash_flow: float | None
    free_cash_flow: float | None
    currency: str


class KeyRatios(BaseModel):
    ticker: str
    as_of: date
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    roe: float | None
    roic: float | None
    fcf_yield: float | None
    debt_to_equity: float | None
    current_ratio: float | None
    net_margin: float | None
    revenue_growth_yoy: float | None


def _period_label(ts: Any) -> str:
    """Turn a pandas Timestamp into a fiscal-year label."""
    year = getattr(ts, "year", None)
    if year is None:
        return "unknown"
    return f"FY{year}"


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        f = float(value)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


async def fetch_ticker_info(ticker: str) -> TickerInfo:
    def _sync() -> TickerInfo:
        t = _make_ticker(ticker)
        info = t.info or {}
        fast = getattr(t, "fast_info", None)
        last_price = _float_or_none(getattr(fast, "last_price", None))
        market_cap = info.get("marketCap") or getattr(fast, "market_cap", None)
        return TickerInfo(
            ticker=ticker.upper(),
            name=info.get("shortName") or info.get("longName") or ticker.upper(),
            currency=info.get("currency", "USD"),
            market_cap=int(market_cap) if market_cap else None,
            last_price=last_price,
        )
    return await asyncio.to_thread(_sync)


async def fetch_financials(ticker: str, periods: int = 4) -> list[Financials]:
    def _sync() -> list[Financials]:
        t = _make_ticker(ticker)
        info = t.info or {}
        currency = info.get("currency", "USD")
        income = getattr(t, "income_stmt", None)
        cashflow = getattr(t, "cashflow", None)
        if income is None or income.empty:
            return []
        out: list[Financials] = []
        cols = list(income.columns)[:periods]
        for col in cols:
            rev = _float_or_none(income[col].get("Total Revenue"))
            ni = _float_or_none(income[col].get("Net Income"))
            ocf = None
            fcf = None
            if cashflow is not None and not cashflow.empty and col in cashflow.columns:
                ocf = _float_or_none(cashflow[col].get("Operating Cash Flow"))
                fcf = _float_or_none(cashflow[col].get("Free Cash Flow"))
            out.append(
                Financials(
                    ticker=ticker.upper(),
                    period=_period_label(col),
                    revenue=rev,
                    net_income=ni,
                    operating_cash_flow=ocf,
                    free_cash_flow=fcf,
                    currency=currency,
                )
            )
        return out
    return await asyncio.to_thread(_sync)


async def fetch_key_ratios(ticker: str) -> KeyRatios:
    def _sync() -> KeyRatios:
        t = _make_ticker(ticker)
        info = t.info or {}
        return KeyRatios(
            ticker=ticker.upper(),
            as_of=date.today(),
            pe_ttm=_float_or_none(info.get("trailingPE")),
            pb=_float_or_none(info.get("priceToBook")),
            ps_ttm=_float_or_none(info.get("priceToSalesTrailing12Months")),
            roe=_float_or_none(info.get("returnOnEquity")),
            roic=_float_or_none(info.get("returnOnAssets")),  # approx; ROIC isn't in yfinance
            fcf_yield=None,
            debt_to_equity=_float_or_none(info.get("debtToEquity")),
            current_ratio=_float_or_none(info.get("currentRatio")),
            net_margin=_float_or_none(info.get("profitMargins")),
            revenue_growth_yoy=_float_or_none(info.get("revenueGrowth")),
        )
    return await asyncio.to_thread(_sync)
```

- [ ] **Step 6: Run the tests, confirm they pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v
```

Expected: `4 passed`.

- [ ] **Step 7: Full backend suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

If mypy flags `yfinance` for missing stubs, the `# type: ignore[import-untyped]` on the import already handles it.

- [ ] **Step 8: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/pyproject.toml backend/uv.lock backend/app/data backend/tests/test_yfinance_adapter.py
git commit -m "feat(backend): add yfinance adapter for ticker info, financials, ratios

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: SEC EDGAR adapter

**Files:**
- Create: `backend/app/data/edgar.py`
- Create: `backend/tests/test_edgar.py`

**Goal:** Async HTTP wrapper for SEC EDGAR's two free JSON endpoints. Maintains an in-process ticker→CIK lookup table (refreshed at most once per process). Fetches filing lists. Tests mock httpx with `respx`.

SEC requires a User-Agent header with a contact email. We use a fixed string that includes the project name and the user's email (read from settings) — see implementation.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_edgar.py`:

```python
# backend/tests/test_edgar.py
import json

import pytest
import respx
from httpx import Response


TICKER_LOOKUP_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
}


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    from app.data.edgar import _clear_caches
    _clear_caches()


@pytest.mark.asyncio
async def test_resolve_cik_for_known_ticker() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import resolve_cik
        cik = await resolve_cik("AAPL")
    assert cik == "0000320193"


@pytest.mark.asyncio
async def test_resolve_cik_unknown_returns_none() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import resolve_cik
        assert await resolve_cik("NOPE") is None


@pytest.mark.asyncio
async def test_fetch_filings_returns_typed_refs() -> None:
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-Q", "8-K", "10-Q"],
                "filingDate": ["2024-11-01", "2024-08-02", "2024-07-30", "2024-05-03"],
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000099",
                    "0000320193-24-000088",
                    "0000320193-24-000060",
                ],
                "primaryDocument": [
                    "aapl-20240928.htm",
                    "aapl-20240629.htm",
                    "aapl-8k.htm",
                    "aapl-20240330.htm",
                ],
            }
        }
    }

    with respx.mock(base_url="https://www.sec.gov") as mock_sec, \
         respx.mock(base_url="https://data.sec.gov") as mock_data:
        mock_sec.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        mock_data.get("/submissions/CIK0000320193.json").mock(
            return_value=Response(200, json=submissions)
        )
        from app.data.edgar import fetch_filings
        refs = await fetch_filings("AAPL", types=["10-K", "10-Q"], limit=5)

    assert len(refs) == 3
    assert refs[0].type == "10-K"
    assert refs[0].accession == "0000320193-24-000123"
    assert refs[0].url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert refs[1].type == "10-Q"
    assert refs[2].type == "10-Q"


@pytest.mark.asyncio
async def test_fetch_filings_unknown_ticker_returns_empty() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import fetch_filings
        refs = await fetch_filings("NOPE")
    assert refs == []
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_edgar.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.edgar'`.

- [ ] **Step 3: Create `backend/app/data/edgar.py`**

```python
# backend/app/data/edgar.py
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel


_SEC_BASE = "https://www.sec.gov"
_DATA_BASE = "https://data.sec.gov"
_USER_AGENT = "Stylobate research-bot (contact: rakhisinha100896@gmail.com)"


class FilingRef(BaseModel):
    type: str
    date: date
    url: str
    accession: str | None = None
    summary: str | None = None


_ticker_cik_cache: dict[str, str] | None = None


def _clear_caches() -> None:
    """Test helper. Resets the in-process ticker→CIK lookup."""
    global _ticker_cik_cache
    _ticker_cik_cache = None


async def _load_ticker_cik_map() -> dict[str, str]:
    global _ticker_cik_cache
    if _ticker_cik_cache is not None:
        return _ticker_cik_cache
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=10.0) as c:
        resp = await c.get(f"{_SEC_BASE}/files/company_tickers.json")
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json()
    table: dict[str, str] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik_int = entry.get("cik_str")
        if ticker and cik_int is not None:
            table[ticker] = f"{int(cik_int):010d}"
    _ticker_cik_cache = table
    return table


async def resolve_cik(ticker: str) -> str | None:
    table = await _load_ticker_cik_map()
    return table.get(ticker.upper())


async def fetch_filings(
    ticker: str,
    types: list[str] | None = None,
    limit: int = 5,
) -> list[FilingRef]:
    cik = await resolve_cik(ticker)
    if cik is None:
        return []

    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=15.0) as c:
        resp = await c.get(f"{_DATA_BASE}/submissions/CIK{cik}.json")
        resp.raise_for_status()
        submissions: dict[str, Any] = resp.json()

    recent: dict[str, Any] = submissions.get("filings", {}).get("recent", {})
    forms: list[str] = recent.get("form", [])
    dates: list[str] = recent.get("filingDate", [])
    accs: list[str] = recent.get("accessionNumber", [])
    docs: list[str] = recent.get("primaryDocument", [])

    cik_nz = str(int(cik))   # strip leading zeros for the Archives URL
    wanted = set(t.upper() for t in (types or ["10-K", "10-Q", "8-K"]))

    refs: list[FilingRef] = []
    for form, dt, acc, doc in zip(forms, dates, accs, docs, strict=False):
        if form.upper() not in wanted:
            continue
        acc_no_dashes = acc.replace("-", "")
        url = f"{_SEC_BASE}/Archives/edgar/data/{cik_nz}/{acc_no_dashes}/{doc}"
        refs.append(
            FilingRef(
                type=form,
                date=date.fromisoformat(dt),
                url=url,
                accession=acc,
            )
        )
        if len(refs) >= limit:
            break
    return refs
```

- [ ] **Step 4: Run the tests, confirm they pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_edgar.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Smoke check against real EDGAR (optional; do NOT add to CI)**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run python3 -c "
import asyncio
from app.data.edgar import fetch_filings
print(asyncio.run(fetch_filings('AAPL', types=['10-K','10-Q'], limit=3)))
"
```

Expected: a list of 3 `FilingRef` objects with real AAPL filings. (Skip if you'd rather not hit SEC during this session — covered by the mocked tests.)

- [ ] **Step 6: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/data/edgar.py backend/tests/test_edgar.py
git commit -m "feat(backend): add SEC EDGAR adapter (CIK lookup + filings list)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Tool functions (financials, ratios, filings)

**Files:**
- Create: `backend/app/tools/__init__.py` (empty)
- Create: `backend/app/tools/base.py`
- Create: `backend/app/tools/financials.py`
- Create: `backend/app/tools/filings.py`
- Create: `backend/tests/test_tools_financials.py`
- Create: `backend/tests/test_tools_filings.py`

**Goal:** Typed tool functions agents will call. Each tool is `async def tool_name(...) -> SomeModel` and has an associated Anthropic-format tool schema. `base.py` defines a `Tool` dataclass holding the schema + the implementation; agents use `Tool.schema` for Anthropic and call `Tool.impl(**args)` to execute.

- [ ] **Step 1: Create `backend/app/tools/__init__.py`** (empty)

```bash
touch backend/app/tools/__init__.py
```

- [ ] **Step 2: Create `backend/app/tools/base.py`**

```python
# backend/app/tools/base.py
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    impl: Callable[..., Awaitable[BaseModel]]

    @property
    def schema(self) -> dict[str, Any]:
        """The Anthropic tool-use schema (what we pass into messages.create)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
```

- [ ] **Step 3: Write the failing test for financials tools**

`backend/tests/test_tools_financials.py`:

```python
# backend/tests/test_tools_financials.py
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Financials, KeyRatios


@pytest.mark.asyncio
async def test_get_financials_returns_list() -> None:
    fake_data = [
        Financials(
            ticker="AAPL", period="FY2024", revenue=391_035_000_000,
            net_income=93_736_000_000, operating_cash_flow=118_254_000_000,
            free_cash_flow=108_807_000_000, currency="USD",
        ),
    ]
    with patch("app.tools.financials._fetch_financials", new=AsyncMock(return_value=fake_data)):
        from app.tools.financials import get_financials_tool
        result = await get_financials_tool.impl(ticker="AAPL", periods=4)
    # Wrapper returns a single pydantic model with a `periods` list
    assert result.ticker == "AAPL"
    assert len(result.periods) == 1
    assert result.periods[0].revenue == 391_035_000_000


@pytest.mark.asyncio
async def test_get_financials_tool_schema_shape() -> None:
    from app.tools.financials import get_financials_tool
    schema = get_financials_tool.schema
    assert schema["name"] == "get_financials"
    assert "ticker" in schema["input_schema"]["properties"]
    assert "periods" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["ticker"]


@pytest.mark.asyncio
async def test_get_key_ratios_returns_model() -> None:
    fake_ratios = KeyRatios(
        ticker="AAPL", as_of=date(2026, 5, 14),
        pe_ttm=29.5, pb=50.1, ps_ttm=8.8, roe=1.45,
        roic=0.30, fcf_yield=None, debt_to_equity=152.0,
        current_ratio=0.98, net_margin=0.255, revenue_growth_yoy=0.062,
    )
    with patch("app.tools.financials._fetch_key_ratios", new=AsyncMock(return_value=fake_ratios)):
        from app.tools.financials import get_key_ratios_tool
        result = await get_key_ratios_tool.impl(ticker="AAPL")
    assert result.pe_ttm == 29.5
    assert result.net_margin == 0.255
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_financials.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.tools.financials'`.

- [ ] **Step 5: Create `backend/app/tools/financials.py`**

```python
# backend/app/tools/financials.py
from pydantic import BaseModel

from app.data.yfinance_adapter import (
    Financials,
    KeyRatios,
    fetch_financials as _fetch_financials,
    fetch_key_ratios as _fetch_key_ratios,
)
from app.tools.base import Tool


class FinancialsResult(BaseModel):
    ticker: str
    periods: list[Financials]


async def _impl_get_financials(ticker: str, periods: int = 4) -> FinancialsResult:
    rows = await _fetch_financials(ticker=ticker, periods=periods)
    return FinancialsResult(ticker=ticker.upper(), periods=rows)


get_financials_tool = Tool(
    name="get_financials",
    description=(
        "Fetch income statement + cash flow for the last N periods (annual). "
        "Returns revenue, net income, operating cash flow, free cash flow."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
            "periods": {"type": "integer", "default": 4, "minimum": 1, "maximum": 10},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_financials,
)


async def _impl_get_key_ratios(ticker: str) -> KeyRatios:
    return await _fetch_key_ratios(ticker=ticker)


get_key_ratios_tool = Tool(
    name="get_key_ratios",
    description=(
        "Fetch valuation + profitability + leverage ratios for the ticker. "
        "Returns P/E, P/B, P/S, ROE, ROIC, FCF yield, D/E, current ratio, net margin, revenue growth YoY."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_key_ratios,
)
```

- [ ] **Step 6: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_financials.py -v
```

Expected: `3 passed`.

- [ ] **Step 7: Write the failing test for filings tool**

`backend/tests/test_tools_filings.py`:

```python
# backend/tests/test_tools_filings.py
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.data.edgar import FilingRef


@pytest.mark.asyncio
async def test_get_filings_returns_typed_list() -> None:
    fake = [
        FilingRef(type="10-K", date=date(2024, 11, 1),
                  url="https://www.sec.gov/Archives/edgar/data/320193/aapl-10k.htm",
                  accession="0000320193-24-000123"),
        FilingRef(type="10-Q", date=date(2024, 8, 2),
                  url="https://www.sec.gov/Archives/edgar/data/320193/aapl-10q.htm",
                  accession="0000320193-24-000099"),
    ]
    with patch("app.tools.filings._fetch_filings", new=AsyncMock(return_value=fake)):
        from app.tools.filings import get_filings_tool
        result = await get_filings_tool.impl(ticker="AAPL", types=["10-K", "10-Q"], limit=5)
    assert result.ticker == "AAPL"
    assert len(result.filings) == 2
    assert result.filings[0].type == "10-K"


def test_get_filings_tool_schema_shape() -> None:
    from app.tools.filings import get_filings_tool
    s = get_filings_tool.schema
    assert s["name"] == "get_filings"
    assert "ticker" in s["input_schema"]["properties"]
    assert "types" in s["input_schema"]["properties"]
    assert s["input_schema"]["required"] == ["ticker"]
```

- [ ] **Step 8: Create `backend/app/tools/filings.py`**

```python
# backend/app/tools/filings.py
from pydantic import BaseModel

from app.data.edgar import FilingRef, fetch_filings as _fetch_filings
from app.tools.base import Tool


class FilingsResult(BaseModel):
    ticker: str
    filings: list[FilingRef]


async def _impl_get_filings(
    ticker: str,
    types: list[str] | None = None,
    limit: int = 5,
) -> FilingsResult:
    refs = await _fetch_filings(ticker=ticker, types=types, limit=limit)
    return FilingsResult(ticker=ticker.upper(), filings=refs)


get_filings_tool = Tool(
    name="get_filings",
    description=(
        "List recent SEC filings for the ticker. types defaults to ['10-K', '10-Q', '8-K']. "
        "Returns at most `limit` filings with type, date, accession, and URL."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filing types to include. Default: 10-K, 10-Q, 8-K.",
            },
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_filings,
)
```

- [ ] **Step 9: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_filings.py -v
```

Expected: `2 passed`.

- [ ] **Step 10: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 11: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools backend/tests/test_tools_financials.py backend/tests/test_tools_filings.py
git commit -m "feat(backend): add typed tool functions for financials, ratios, filings

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Ticker Resolver agent

**Files:**
- Create: `backend/app/prompts/ticker_resolver.md`
- Create: `backend/app/agents/__init__.py` (empty)
- Create: `backend/app/agents/ticker_resolver.py`
- Create: `backend/tests/test_ticker_resolver.py`

**Goal:** Given free-text input like "Apple" or "tell me about TSLA", return a `TickerResolution` with the canonical ticker, market, asset class, and a confidence. Calls Haiku 4.5 with a single tool the model can use to emit its structured answer. No data lookups happen in Phase 1 — the resolver relies purely on the LLM's knowledge.

- [ ] **Step 1: Create `backend/app/agents/__init__.py`** (empty)

```bash
touch backend/app/agents/__init__.py
```

- [ ] **Step 2: Create the system prompt**

`backend/app/prompts/ticker_resolver.md`:

```markdown
You are a ticker resolver inside the Stylobate research platform. Your only job is to identify the stock or crypto ticker the user is asking about and emit a single `resolve` tool call with the typed result.

Rules:
- Always return the canonical ticker symbol with market suffix:
  - US listings: bare symbol, e.g. AAPL, MSFT, BRK.B
  - NSE (India): with `.NS` suffix, e.g. RELIANCE.NS, HDFCBANK.NS
  - BSE (India): with `.BO` suffix, e.g. RELIANCE.BO
  - Major crypto: BTC, ETH (no suffix), market = CRYPTO
- If the input is ambiguous (e.g., "Reliance" could be RELIANCE.NS or RELIANCE.BO), pick the more liquid venue (NSE for India), set confidence ≤ 0.7, and list the alternative in `candidates`.
- If the input doesn't resemble a ticker or company name at all, return confidence 0.0 and ticker = "" with `candidates = []`.
- Never guess private companies or non-listed names.
- Always call the `resolve` tool exactly once. Do not emit any text outside the tool call.
```

- [ ] **Step 3: Write the failing test**

`backend/tests/test_ticker_resolver.py`:

```python
# backend/tests/test_ticker_resolver.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use_response(args: dict[str, Any]) -> Any:
    """Build a fake Anthropic message with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "resolve"
    block.input = args
    block.id = "toolu_test_1"
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    resp.usage = MagicMock(input_tokens=10, output_tokens=10,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


@pytest.mark.asyncio
async def test_resolve_us_stock() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
        "asset_class": "equity", "confidence": 0.95, "candidates": [],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("Apple", client=fake_client)
    assert result.ticker == "AAPL"
    assert result.market == "US"
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_resolve_ambiguous_indian() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "RELIANCE.NS", "name": "Reliance Industries Ltd.",
        "market": "IN", "asset_class": "equity", "confidence": 0.65,
        "candidates": ["RELIANCE.BO"],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("Reliance", client=fake_client)
    assert result.ticker == "RELIANCE.NS"
    assert result.candidates == ["RELIANCE.BO"]


@pytest.mark.asyncio
async def test_resolve_returns_low_confidence_for_garbage() -> None:
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_tool_use_response({
        "ticker": "", "name": "", "market": "US",
        "asset_class": "equity", "confidence": 0.0, "candidates": [],
    }))
    from app.agents.ticker_resolver import resolve_ticker
    result = await resolve_ticker("asdfqwer", client=fake_client)
    assert result.confidence == 0.0
    assert result.ticker == ""
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_ticker_resolver.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.ticker_resolver'`.

- [ ] **Step 5: Create `backend/app/agents/ticker_resolver.py`**

```python
# backend/app/agents/ticker_resolver.py
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.core.anthropic_client import get_client


class TickerResolution(BaseModel):
    ticker: str
    name: str
    market: Literal["US", "IN", "CRYPTO"]
    asset_class: Literal["equity", "etf", "crypto"]
    confidence: float
    candidates: list[str] = []


_RESOLVE_TOOL: dict[str, Any] = {
    "name": "resolve",
    "description": "Emit the resolved ticker for the user's input. Call this exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "name": {"type": "string"},
            "market": {"type": "string", "enum": ["US", "IN", "CRYPTO"]},
            "asset_class": {"type": "string", "enum": ["equity", "etf", "crypto"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "candidates": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ticker", "name", "market", "asset_class", "confidence"],
    },
}


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "ticker_resolver.md"
    return path.read_text(encoding="utf-8")


async def resolve_ticker(
    user_text: str,
    *,
    client: AsyncAnthropic | Any | None = None,
) -> TickerResolution:
    c = client or get_client()
    sys = _load_prompt()
    resp = await c.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        temperature=0.0,
        system=[{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}],
        tools=[_RESOLVE_TOOL],
        tool_choice={"type": "tool", "name": "resolve"},
        messages=[{"role": "user", "content": user_text}],
    )
    # Pull the tool_use block — there must be exactly one with name "resolve".
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "resolve":
            args = cast(dict[str, Any], block.input)
            args.setdefault("candidates", [])
            return TickerResolution(**args)
    raise RuntimeError("ticker_resolver: no resolve tool call in response")
```

- [ ] **Step 6: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_ticker_resolver.py -v
```

Expected: `3 passed`.

- [ ] **Step 7: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 8: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents backend/app/prompts/ticker_resolver.md backend/tests/test_ticker_resolver.py
git commit -m "feat(backend): add Ticker Resolver agent (Haiku, structured tool)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Fundamental Analyst agent

**Files:**
- Create: `backend/app/prompts/fundamental.md`
- Create: `backend/app/agents/fundamental.py`
- Create: `backend/tests/test_fundamental_agent.py`

**Goal:** Given a ticker and a brief, run a Sonnet conversation with three tools (`get_financials`, `get_key_ratios`, `get_filings`). Loop on tool calls until the model emits a final `submit_findings` tool call with a typed `FundamentalFindings`. Returns that payload.

- [ ] **Step 1: Create the system prompt**

`backend/app/prompts/fundamental.md`:

```markdown
You are the Fundamental Analyst inside Stylobate, a multi-agent research platform.

Your job: produce a tight, evidence-based fundamentals review of one ticker for the Lead Banker. Stick to the data you can fetch via your tools. Do not invent numbers.

Process:
1. Use `get_key_ratios` and `get_financials` to ground your view of valuation, margins, and growth.
2. Use `get_filings` to identify the most recent 10-K / 10-Q / 8-K. Reference at least one filing if the question requires recency.
3. When you have enough data, call `submit_findings` exactly once with a typed `FundamentalFindings` object. Stop after that — do not emit text after.

Discipline:
- Every numeric claim in `fundamentals_summary` and `risks` must be supported by a tool result. Note the source in `citations`.
- Use the company's reporting currency. Don't convert.
- Risks should be specific (e.g., "data center revenue concentrated at top 4 hyperscalers (~40%)"), not generic ("competition").
- Confidence: 0.9+ only if you have both ratios and at least one filing reference; otherwise <0.7.
- Be concise. Thesis = 2–4 sentences. Bullets = 3–6 each.
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_fundamental_agent.py`:

```python
# backend/tests/test_fundamental_agent.py
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.edgar import FilingRef
from app.data.yfinance_adapter import Financials, KeyRatios


def _tool_use_block(name: str, args: dict[str, Any], tool_id: str = "toolu_1") -> Any:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = args
    block.id = tool_id
    return block


def _message(*blocks: Any, stop_reason: str = "tool_use") -> Any:
    msg = MagicMock()
    msg.content = list(blocks)
    msg.stop_reason = stop_reason
    msg.usage = MagicMock(
        input_tokens=100, output_tokens=20,
        cache_creation_input_tokens=80, cache_read_input_tokens=0,
    )
    return msg


@pytest.mark.asyncio
async def test_fundamental_agent_runs_tool_loop_to_submit_findings() -> None:
    # Turn 1: model calls get_key_ratios
    # Turn 2: model calls get_financials
    # Turn 3: model calls get_filings
    # Turn 4: model submits findings
    fake_ratios = KeyRatios(
        ticker="AAPL", as_of=date(2026, 5, 14),
        pe_ttm=29.5, pb=50.1, ps_ttm=8.8, roe=1.45,
        roic=0.30, fcf_yield=None, debt_to_equity=152.0,
        current_ratio=0.98, net_margin=0.255, revenue_growth_yoy=0.062,
    )
    fake_financials = [
        Financials(ticker="AAPL", period="FY2024", revenue=391e9,
                   net_income=93.7e9, operating_cash_flow=118e9,
                   free_cash_flow=108e9, currency="USD"),
    ]
    fake_filings = [
        FilingRef(type="10-K", date=date(2024, 11, 1),
                  url="https://sec.gov/edgar/aapl-10k.htm",
                  accession="0000320193-24-000123"),
    ]

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_key_ratios", {"ticker": "AAPL"}, "t1")),
        _message(_tool_use_block("get_financials", {"ticker": "AAPL", "periods": 2}, "t2")),
        _message(_tool_use_block("get_filings", {"ticker": "AAPL", "limit": 3}, "t3")),
        _message(
            _tool_use_block("submit_findings", {
                "ticker": "AAPL",
                "thesis": "Strong cash generation; valuation rich vs history.",
                "fundamentals_summary": [
                    "Net margin 25.5% (yfinance)",
                    "Revenue FY24 $391B, +2% YoY",
                ],
                "risks": [
                    "P/E 29.5x vs 5y avg ~22x",
                    "China demand softness referenced in 10-K",
                ],
                "citations": [
                    {"source": "yfinance", "ref": "yfinance:ratios:AAPL"},
                    {"source": "edgar", "ref": "AAPL 10-K 2024-11-01"},
                ],
                "confidence": 0.9,
            }, "t4"),
            stop_reason="tool_use",
        ),
    ])

    # Patch the tool implementations to return our fakes
    with (
        # patches use the tools module's exported singletons
        # so we override their .impl attribute directly
        pytest.MonkeyPatch.context() as mp,
    ):
        from app.tools.financials import get_financials_tool, get_key_ratios_tool
        from app.tools.filings import get_filings_tool
        from app.tools.financials import FinancialsResult
        from app.tools.filings import FilingsResult

        mp.setattr(
            get_key_ratios_tool, "impl",
            AsyncMock(return_value=fake_ratios),
        )
        mp.setattr(
            get_financials_tool, "impl",
            AsyncMock(return_value=FinancialsResult(ticker="AAPL", periods=fake_financials)),
        )
        mp.setattr(
            get_filings_tool, "impl",
            AsyncMock(return_value=FilingsResult(ticker="AAPL", filings=fake_filings)),
        )

        from app.agents.fundamental import run_fundamental_analysis
        findings = await run_fundamental_analysis(
            ticker="AAPL",
            brief="Quick fundamentals review",
            client=fake_client,
        )

    assert findings.ticker == "AAPL"
    assert findings.confidence == 0.9
    assert len(findings.citations) == 2
    # Verify the tool loop ran exactly 4 messages.create calls
    assert fake_client.messages.create.await_count == 4


@pytest.mark.asyncio
async def test_fundamental_agent_raises_if_no_submit_findings() -> None:
    fake_client = MagicMock()
    # Model just keeps emitting text without ever calling submit_findings
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.fundamental import FundamentalError, run_fundamental_analysis
    with pytest.raises(FundamentalError):
        await run_fundamental_analysis(ticker="AAPL", brief="hi", client=fake_client)
```

- [ ] **Step 3: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fundamental_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.fundamental'`.

- [ ] **Step 4: Create `backend/app/agents/fundamental.py`**

```python
# backend/app/agents/fundamental.py
import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.filings import get_filings_tool
from app.tools.financials import get_financials_tool, get_key_ratios_tool


_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 8


class Citation(BaseModel):
    source: str
    ref: str
    snippet: str | None = None


class FundamentalFindings(BaseModel):
    ticker: str
    thesis: str
    fundamentals_summary: list[str]
    risks: list[str]
    citations: list[Citation]
    confidence: float


class FundamentalError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_findings",
    "description": "Emit the final FundamentalFindings and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "thesis": {"type": "string"},
            "fundamentals_summary": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "ref": {"type": "string"},
                        "snippet": {"type": "string"},
                    },
                    "required": ["source", "ref"],
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["ticker", "thesis", "fundamentals_summary", "risks", "citations", "confidence"],
    },
}


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "fundamental.md"
    return path.read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_key_ratios_tool.name: get_key_ratios_tool,
    get_financials_tool.name: get_financials_tool,
    get_filings_tool.name: get_filings_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_fundamental_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> FundamentalFindings:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [
        {"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}},
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Ticker: {ticker}\nBrief: {brief}\nUse your tools and submit_findings when ready.",
        }
    ]

    for _ in range(_MAX_TURNS):
        resp = await c.messages.create(
            model=_MODEL,
            max_tokens=2000,
            temperature=0.3,
            system=system_blocks,
            tools=_all_tool_schemas(),
            messages=messages,
        )

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            raise FundamentalError("model emitted no tool calls; expected at least submit_findings")

        # Append the assistant turn
        messages.append({"role": "assistant", "content": resp.content})

        # Build the user turn with tool_results
        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            if tu.name == "submit_findings":
                args = cast(dict[str, Any], tu.input)
                # Validate via pydantic before returning
                return FundamentalFindings(**args)
            tool = _TOOLS_BY_NAME.get(tu.name)
            if tool is None:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"error": f"unknown tool: {tu.name}"}),
                    "is_error": True,
                })
                continue
            try:
                payload = await tool.impl(**cast(dict[str, Any], tu.input))
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": payload.model_dump_json(),
                })
            except Exception as e:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results_content})

    raise FundamentalError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit_findings")
```

- [ ] **Step 5: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fundamental_agent.py -v
```

Expected: `2 passed`.

- [ ] **Step 6: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/fundamental.md backend/app/agents/fundamental.py backend/tests/test_fundamental_agent.py
git commit -m "feat(backend): add Fundamental Analyst agent (Sonnet, tool loop)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Lead Banker agent + emit tools + orchestration

**Files:**
- Create: `backend/app/prompts/lead_banker.md`
- Create: `backend/app/agents/lead_banker.py`
- Create: `backend/tests/test_lead_banker_agent.py`

**Goal:** The orchestrator. Reads the chat history + the ticker resolution + the Fundamental findings, then emits a stream of typed deltas via "emit" tools. Each `emit_*` tool call corresponds to one SSE frame the route will serialize.

The Lead Banker calls `dispatch` once (the route handler dispatches Fundamental in Phase 1; in Phase 2+ multiple specialists in parallel). The agent then synthesizes the response.

For Phase 1, the orchestration is simpler than the full §4.5 spec: the route invokes Fundamental directly (no real dispatch tool yet — that comes in Phase 2 when there are multiple specialists). Lead Banker receives the findings as part of its context and is responsible only for synthesis + emit_*.

- [ ] **Step 1: Create the system prompt**

`backend/app/prompts/lead_banker.md`:

```markdown
You are the Lead Banker inside Stylobate. You synthesize specialist findings into a streamed research note for the user.

You receive:
- The user's question.
- The resolved ticker.
- The Fundamental Analyst's findings.

You produce a streamed response by calling the following tools in this order:

1. `emit_quick_take` — one short signal line.
2. `emit_stock_card` — basic price + key stats card.
3. `emit_section` — at least three sections: "Thesis", "Fundamentals", "Risks". Each section's `markdown` should reference the citation badges that exist in `citations` by their index, like `[1]` and `[2]`.
4. `emit_recommendation` — directional call + position-size range + entry zone + stop + 12-mo target.
5. `emit_disclaimer` — always last before `emit_done`. The disclaimer text is fixed.
6. `emit_done` — signals the end of streaming.

Discipline:
- Every numeric in `emit_section` markdown must be supported by an item in the `citations` array of the section call.
- Recommendation `signal` is one of: `tactical_buy`, `accumulate`, `hold`, `reduce`. No "definitely buy" / no specific dollar amounts.
- `position_size_range` is a tuple of percentages of the user's portfolio, e.g. [2, 4].
- The disclaimer is verbatim: "Educational analysis, not personalized investment advice. Do your own diligence and consider your tax situation."
- Do NOT emit any plain text. Only tool calls.
- Stop after `emit_done`.
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_lead_banker_agent.py`:

```python
# backend/tests/test_lead_banker_agent.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.fundamental import Citation, FundamentalFindings
from app.agents.ticker_resolver import TickerResolution


def _tool_use(name: str, args: dict[str, Any], id_: str) -> Any:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = args
    block.id = id_
    return block


def _message(*blocks: Any, stop_reason: str = "end_turn") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=100, output_tokens=50,
                        cache_creation_input_tokens=80, cache_read_input_tokens=20)
    return m


@pytest.mark.asyncio
async def test_lead_banker_streams_expected_deltas() -> None:
    findings = FundamentalFindings(
        ticker="AAPL",
        thesis="Strong cash generation.",
        fundamentals_summary=["Net margin 25.5%"],
        risks=["P/E rich"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_message(
        _tool_use("emit_quick_take", {"signal": "tactical_buy", "qualifier": "valuation rich"}, "1"),
        _tool_use("emit_stock_card", {
            "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
            "currency": "USD", "stats": {"P/E": "29.5", "Net margin": "25.5%"},
        }, "2"),
        _tool_use("emit_section", {
            "title": "Thesis",
            "markdown": "Strong cash generation [1].",
            "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}],
        }, "3"),
        _tool_use("emit_section", {
            "title": "Fundamentals",
            "markdown": "Net margin 25.5% [1].",
            "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}],
        }, "4"),
        _tool_use("emit_section", {
            "title": "Risks",
            "markdown": "P/E rich.",
            "citations": [],
        }, "5"),
        _tool_use("emit_recommendation", {
            "signal": "tactical_buy",
            "position_size_range": [2, 4],
            "entry_zone": "440-455",
            "stop": "385",
            "target_12mo_base": "540",
        }, "6"),
        _tool_use("emit_disclaimer", {}, "7"),
        _tool_use("emit_done", {}, "8"),
        stop_reason="end_turn",
    ))

    from app.agents.lead_banker import run_lead_banker
    deltas = []
    async for d in run_lead_banker(
        user_message="Should I buy AAPL?",
        resolution=resolution,
        fundamental_findings=findings,
        client=fake_client,
    ):
        deltas.append(d)

    types = [d["type"] for d in deltas]
    assert types == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]
    # Section titles
    sections = [d for d in deltas if d["type"] == "section"]
    assert [s["title"] for s in sections] == ["Thesis", "Fundamentals", "Risks"]
```

- [ ] **Step 3: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.lead_banker'`.

- [ ] **Step 4: Create `backend/app/agents/lead_banker.py`**

```python
# backend/app/agents/lead_banker.py
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from app.agents.fundamental import FundamentalFindings
from app.agents.ticker_resolver import TickerResolution
from app.core.anthropic_client import get_client


_MODEL = "claude-opus-4-7"


_EMIT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "emit_quick_take",
        "description": "Stream a single-line quick take.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal": {"type": "string", "enum": ["tactical_buy", "accumulate", "hold", "reduce"]},
                "qualifier": {"type": "string"},
            },
            "required": ["signal", "qualifier"],
        },
    },
    {
        "name": "emit_stock_card",
        "description": "Stream the stock identity + price + key stats card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "name": {"type": "string"},
                "market": {"type": "string"},
                "currency": {"type": "string"},
                "stats": {"type": "object"},
            },
            "required": ["ticker", "name", "market", "currency", "stats"],
        },
    },
    {
        "name": "emit_section",
        "description": "Stream a section of the research note with markdown and citation list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "markdown": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "ref": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                        "required": ["source", "ref"],
                    },
                },
            },
            "required": ["title", "markdown", "citations"],
        },
    },
    {
        "name": "emit_recommendation",
        "description": "Stream the recommendation card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal": {"type": "string", "enum": ["tactical_buy", "accumulate", "hold", "reduce"]},
                "position_size_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "entry_zone": {"type": "string"},
                "stop": {"type": "string"},
                "target_12mo_base": {"type": "string"},
            },
            "required": ["signal", "position_size_range", "entry_zone", "stop", "target_12mo_base"],
        },
    },
    {
        "name": "emit_disclaimer",
        "description": "Stream the educational disclaimer. No arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "emit_done",
        "description": "Signal the end of streaming. No arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_DISCLAIMER_TEXT = (
    "Educational analysis, not personalized investment advice. "
    "Do your own diligence and consider your tax situation."
)


def _load_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "lead_banker.md"
    return path.read_text(encoding="utf-8")


def _build_user_message(
    user_text: str,
    resolution: TickerResolution,
    findings: FundamentalFindings,
) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, {resolution.market})\n\n"
        f"Fundamental Analyst findings:\n{findings.model_dump_json(indent=2)}\n\n"
        "Now synthesize the response by calling the emit_* tools in the prescribed order."
    )


async def run_lead_banker(
    *,
    user_message: str,
    resolution: TickerResolution,
    fundamental_findings: FundamentalFindings,
    client: AsyncAnthropic | Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [
        {"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}},
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_user_message(user_message, resolution, fundamental_findings)},
    ]

    resp = await c.messages.create(
        model=_MODEL,
        max_tokens=4000,
        temperature=0.3,
        system=system_blocks,
        tools=_EMIT_TOOLS,
        messages=messages,
    )

    for block in resp.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        name = getattr(block, "name", "")
        args = cast(dict[str, Any], getattr(block, "input", {})) or {}
        if name == "emit_quick_take":
            yield {"type": "quick_take", **args}
        elif name == "emit_stock_card":
            yield {"type": "stock_card", **args}
        elif name == "emit_section":
            yield {"type": "section", **args}
        elif name == "emit_recommendation":
            yield {"type": "recommendation", **args}
        elif name == "emit_disclaimer":
            yield {"type": "disclaimer", "text": _DISCLAIMER_TEXT}
        elif name == "emit_done":
            yield {"type": "done"}
            return
```

- [ ] **Step 5: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: `1 passed`.

- [ ] **Step 6: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/lead_banker.md backend/app/agents/lead_banker.py backend/tests/test_lead_banker_agent.py
git commit -m "feat(backend): add Lead Banker agent (Opus, emit_* tools for streaming)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Output validator

**Files:**
- Create: `backend/app/core/output_validator.py`
- Create: `backend/tests/test_output_validator.py`

**Goal:** Server-side check that runs over the Lead Banker's deltas before they're streamed to the user. Catches: missing required deltas, recommendation with banned fields, section with numeric claims and zero citations. Returns a `ValidationResult` with issues; route handler decides whether to reject.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_output_validator.py`:

```python
# backend/tests/test_output_validator.py
from typing import Any

import pytest


def _good_deltas() -> list[dict[str, Any]]:
    return [
        {"type": "quick_take", "signal": "tactical_buy", "qualifier": "OK"},
        {"type": "stock_card", "ticker": "AAPL", "name": "Apple Inc.",
         "market": "US", "currency": "USD", "stats": {"P/E": "29"}},
        {"type": "section", "title": "Thesis", "markdown": "Good thesis.",
         "citations": []},
        {"type": "section", "title": "Fundamentals",
         "markdown": "Net margin 25.5% [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}]},
        {"type": "section", "title": "Risks", "markdown": "Some risk.",
         "citations": []},
        {"type": "recommendation", "signal": "tactical_buy",
         "position_size_range": [2, 4], "entry_zone": "440-455",
         "stop": "385", "target_12mo_base": "540"},
        {"type": "disclaimer", "text": "Educational analysis…"},
        {"type": "done"},
    ]


def test_validator_accepts_well_formed_deltas() -> None:
    from app.core.output_validator import validate
    r = validate(_good_deltas())
    assert r.ok is True
    assert r.issues == []


def test_validator_flags_missing_disclaimer() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    deltas = [d for d in deltas if d["type"] != "disclaimer"]
    r = validate(deltas)
    assert r.ok is False
    assert any("disclaimer" in i.lower() for i in r.issues)


def test_validator_flags_missing_recommendation() -> None:
    from app.core.output_validator import validate
    deltas = [d for d in _good_deltas() if d["type"] != "recommendation"]
    r = validate(deltas)
    assert r.ok is False
    assert any("recommendation" in i.lower() for i in r.issues)


def test_validator_flags_section_with_numbers_no_citations() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    # Replace the Fundamentals section with one that has numbers but no citations
    for i, d in enumerate(deltas):
        if d.get("type") == "section" and d.get("title") == "Fundamentals":
            deltas[i] = {"type": "section", "title": "Fundamentals",
                         "markdown": "Net margin 25.5%.", "citations": []}
            break
    r = validate(deltas)
    assert r.ok is False
    assert any("citation" in i.lower() for i in r.issues)


def test_validator_flags_invalid_signal() -> None:
    from app.core.output_validator import validate
    deltas = _good_deltas()
    for i, d in enumerate(deltas):
        if d.get("type") == "recommendation":
            deltas[i] = {**d, "signal": "definitely_buy"}
            break
    r = validate(deltas)
    assert r.ok is False
    assert any("signal" in i.lower() for i in r.issues)
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_output_validator.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.core.output_validator'`.

- [ ] **Step 3: Create `backend/app/core/output_validator.py`**

```python
# backend/app/core/output_validator.py
import re
from typing import Any

from pydantic import BaseModel


_REQUIRED_TYPES = ("recommendation", "disclaimer", "done")
_ALLOWED_SIGNALS = {"tactical_buy", "accumulate", "hold", "reduce"}
_NUMERIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|x|bps|bn|m|tn)?\b", re.IGNORECASE)


class ValidationResult(BaseModel):
    ok: bool
    issues: list[str]


def _has_uncited_numbers(section: dict[str, Any]) -> bool:
    md = section.get("markdown", "")
    citations = section.get("citations", []) or []
    if not _NUMERIC_RE.search(md):
        return False
    return len(citations) == 0


def validate(deltas: list[dict[str, Any]]) -> ValidationResult:
    issues: list[str] = []
    types = [d.get("type", "") for d in deltas]

    for required in _REQUIRED_TYPES:
        if required not in types:
            issues.append(f"missing required delta: {required}")

    for d in deltas:
        t = d.get("type")
        if t == "recommendation":
            sig = d.get("signal")
            if sig not in _ALLOWED_SIGNALS:
                issues.append(f"recommendation signal '{sig}' not in {sorted(_ALLOWED_SIGNALS)}")
            psr = d.get("position_size_range") or []
            if len(psr) != 2:
                issues.append("recommendation position_size_range must have exactly 2 numbers")
        elif t == "section":
            if _has_uncited_numbers(d):
                issues.append(
                    f"section '{d.get('title')}' has numeric claims but no citations"
                )

    return ValidationResult(ok=not issues, issues=issues)
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_output_validator.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Full suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/core/output_validator.py backend/tests/test_output_validator.py
git commit -m "feat(backend): add output validator for Lead Banker deltas

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Replace echo in `/chat/stream` with the real agent flow

**Files:**
- Modify: `backend/app/routes/chat.py`
- Modify: `backend/tests/test_chat_stream.py`

**Goal:** Wire ticker resolver → fundamental analyst → lead banker → output validator → SSE. The user message becomes the input. Each Lead Banker emit becomes one SSE delta frame. Persistence: store the user message before invoking agents; store the assistant message (as the ordered delta list) after the lead banker finishes.

Phase 0's behaviour is gone; the route now performs real LLM work. Tests use AsyncMock to stub the agents.

- [ ] **Step 1: Rewrite `backend/app/routes/chat.py`**

```python
# backend/app/routes/chat.py
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.fundamental import run_fundamental_analysis
from app.agents.lead_banker import run_lead_banker
from app.agents.ticker_resolver import resolve_ticker
from app.core.auth import get_current_token, get_current_user
from app.core.logging import get_logger
from app.core.output_validator import validate as validate_deltas
from app.core.supabase_client import get_user_client
from app.db.messages import get_or_create_chat, insert_message

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger(__name__)


class ChatStreamRequest(BaseModel):
    content: str
    chat_id: str | None = None


def _sse(event: str, data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/stream")
async def chat_stream(
    req: ChatStreamRequest,
    user: dict[str, Any] = Depends(get_current_user),
    token: str = Depends(get_current_token),
) -> StreamingResponse:
    user_id = user["sub"]
    sb = get_user_client(token)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        chat = await get_or_create_chat(sb, user_id=user_id, chat_id=req.chat_id)
        user_msg = await insert_message(
            sb, chat_id=chat.id, role="user",
            content={"type": "text", "text": req.content},
        )
        yield _sse("progress", {"step": "resolving_ticker", "message_id": str(user_msg.id)})

        resolution = await resolve_ticker(req.content)
        if resolution.confidence < 0.4 or not resolution.ticker:
            yield _sse("error", {"message": "I couldn't identify the ticker. Try including the symbol (e.g. AAPL)."})
            yield _sse("done", {"message_id": None, "chat_id": str(chat.id)})
            return

        yield _sse("progress", {"step": "running_fundamentals", "ticker": resolution.ticker})
        findings = await run_fundamental_analysis(
            ticker=resolution.ticker,
            brief=f"User asked: {req.content}",
        )

        yield _sse("progress", {"step": "synthesizing"})

        deltas: list[dict[str, Any]] = []
        async for d in run_lead_banker(
            user_message=req.content,
            resolution=resolution,
            fundamental_findings=findings,
        ):
            deltas.append(d)
            if d.get("type") == "done":
                continue
            yield _sse("delta", d)

        v = validate_deltas(deltas)
        if not v.ok:
            log.warning("output_validator_failed", issues=v.issues)
            yield _sse("error", {"message": "Output failed validation: " + "; ".join(v.issues[:3])})

        asst_msg = await insert_message(
            sb, chat_id=chat.id, role="assistant", content=deltas,
        )
        yield _sse("done", {"message_id": str(asst_msg.id), "chat_id": str(chat.id)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: Update `backend/tests/test_chat_stream.py`**

Replace the existing file with:

```python
# backend/tests/test_chat_stream.py
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.agents.fundamental import Citation, FundamentalFindings
from app.agents.ticker_resolver import TickerResolution
from app.db.models import Chat, Message


async def _fake_lead_banker_stream() -> AsyncIterator[dict[str, Any]]:
    for d in [
        {"type": "quick_take", "signal": "tactical_buy", "qualifier": "OK"},
        {"type": "stock_card", "ticker": "AAPL", "name": "Apple Inc.",
         "market": "US", "currency": "USD", "stats": {"P/E": "29.5"}},
        {"type": "section", "title": "Thesis",
         "markdown": "Strong cash generation.", "citations": []},
        {"type": "section", "title": "Fundamentals",
         "markdown": "Net margin 25.5% [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}]},
        {"type": "section", "title": "Risks",
         "markdown": "P/E rich.", "citations": []},
        {"type": "recommendation", "signal": "tactical_buy",
         "position_size_range": [2, 4], "entry_zone": "440-455",
         "stop": "385", "target_12mo_base": "540"},
        {"type": "disclaimer", "text": "Educational analysis…"},
        {"type": "done"},
    ]:
        yield d


@pytest.mark.asyncio
async def test_chat_stream_rejects_unauth(client: AsyncClient) -> None:
    response = await client.post("/chat/stream", json={"content": "deep dive AAPL"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_stream_full_pipeline(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()

    fake_chat = Chat(
        id=chat_id, user_id=uuid.UUID(user_id), title=None,
        model="claude-opus-4-7",
        created_at="2026-05-14T18:00:00+00:00",
        last_message_at="2026-05-14T18:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "deep dive AAPL"},
        created_at="2026-05-14T18:00:00+00:00",
    )
    fake_asst_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="assistant",
        content=[{"type": "done"}],
        created_at="2026-05-14T18:00:02+00:00",
    )

    fake_resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )
    fake_findings = FundamentalFindings(
        ticker="AAPL", thesis="Strong.",
        fundamentals_summary=["Net margin 25.5%"], risks=["P/E"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )

    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message",
              new=AsyncMock(side_effect=[fake_user_msg, fake_asst_msg])),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=fake_resolution)),
        patch("app.routes.chat.run_fundamental_analysis",
              new=AsyncMock(return_value=fake_findings)),
        patch("app.routes.chat.run_lead_banker", return_value=_fake_lead_banker_stream()),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "deep dive AAPL"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    # SSE frame checks
    assert "event: progress" in body
    assert "resolving_ticker" in body
    assert "running_fundamentals" in body
    assert "synthesizing" in body
    assert "event: delta" in body
    assert '"type": "quick_take"' in body
    assert '"type": "stock_card"' in body
    assert '"type": "recommendation"' in body
    assert '"type": "disclaimer"' in body
    assert "event: done" in body
    # 'done' should NOT appear as a delta event payload
    assert body.count("event: done") == 1


@pytest.mark.asyncio
async def test_chat_stream_rejects_low_confidence_resolution(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()
    fake_chat = Chat(
        id=chat_id, user_id=uuid.UUID(user_id), title=None,
        model="claude-opus-4-7",
        created_at="2026-05-14T18:00:00+00:00",
        last_message_at="2026-05-14T18:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "asdfqwer"},
        created_at="2026-05-14T18:00:00+00:00",
    )
    low_conf = TickerResolution(
        ticker="", name="", market="US", asset_class="equity", confidence=0.0,
    )

    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message", new=AsyncMock(return_value=fake_user_msg)),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=low_conf)),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "asdfqwer"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: error" in body
    assert "couldn't identify the ticker" in body
    assert "event: done" in body
```

- [ ] **Step 3: Run the chat tests, confirm they pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_chat_stream.py -v
```

Expected: `3 passed`.

- [ ] **Step 4: Full backend suite stays green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/routes/chat.py backend/tests/test_chat_stream.py
git commit -m "feat(backend): wire ticker resolver + fundamental + lead banker into /chat/stream

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Frontend — render the new typed deltas

**Files:**
- Modify: `frontend/lib/types.ts`
- Create: `frontend/components/stock-card.tsx`
- Create: `frontend/components/recommendation-card.tsx`
- Create: `frontend/components/section-block.tsx`
- Create: `frontend/components/citation-badge.tsx`
- Create: `frontend/components/progress-strip.tsx`
- Create: `frontend/components/disclaimer.tsx`
- Modify: `frontend/components/chat-thread.tsx`
- Modify: `frontend/app/chat/page.tsx`
- Create: `frontend/__tests__/delta-renderers.test.tsx`

**Goal:** Render every delta type from Phase 1 in the chat thread. Tests use Vitest + Testing Library to verify each component renders the expected text.

- [ ] **Step 1: Add `react-markdown` for section rendering**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm install react-markdown rehype-sanitize
npm install -D @testing-library/react @testing-library/jest-dom @testing-library/dom
```

- [ ] **Step 2: Extend the shared types**

`frontend/lib/types.ts` — replace the file with:

```ts
export type Citation = { source: string; ref: string; index?: number };

export type DeltaQuickTake = {
  type: "quick_take";
  signal: "tactical_buy" | "accumulate" | "hold" | "reduce";
  qualifier: string;
};

export type DeltaStockCard = {
  type: "stock_card";
  ticker: string;
  name: string;
  market: string;
  currency: string;
  stats: Record<string, string>;
};

export type DeltaSection = {
  type: "section";
  title: string;
  markdown: string;
  citations: Citation[];
};

export type DeltaRecommendation = {
  type: "recommendation";
  signal: "tactical_buy" | "accumulate" | "hold" | "reduce";
  position_size_range: [number, number];
  entry_zone: string;
  stop: string;
  target_12mo_base: string;
};

export type DeltaDisclaimer = { type: "disclaimer"; text: string };
export type DeltaText = { type: "text"; text: string };

export type DeltaBlock =
  | DeltaQuickTake
  | DeltaStockCard
  | DeltaSection
  | DeltaRecommendation
  | DeltaDisclaimer
  | DeltaText;

export type SSEEvent =
  | { event: "progress"; data: { step: string; ticker?: string; message_id?: string } }
  | { event: "delta"; data: DeltaBlock }
  | { event: "done"; data: { message_id: string | null; chat_id: string } }
  | { event: "error"; data: { message: string } };

export type ChatMessageBlock = DeltaBlock;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: ChatMessageBlock[] | { type: "text"; text: string };
  progress?: { step: string; ticker?: string }[];
}
```

- [ ] **Step 3: Create `frontend/components/citation-badge.tsx`**

```tsx
"use client";
import type { Citation } from "@/lib/types";

export function CitationBadge({ citation }: { citation: Citation }) {
  const label = citation.index
    ? `${citation.source}·${citation.index}`
    : citation.source;
  return (
    <span
      title={citation.ref}
      className="inline-block rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-foreground/70 mx-0.5"
    >
      {label}
    </span>
  );
}
```

- [ ] **Step 4: Create `frontend/components/stock-card.tsx`**

```tsx
"use client";
import type { DeltaStockCard } from "@/lib/types";

export function StockCard({ delta }: { delta: DeltaStockCard }) {
  return (
    <div className="rounded-lg border bg-card p-4 flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="font-semibold text-lg">{delta.ticker}</div>
          <div className="text-xs text-muted-foreground">
            {delta.name} · {delta.market}
          </div>
        </div>
        <div className="text-xs text-muted-foreground">{delta.currency}</div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {Object.entries(delta.stats).map(([k, v]) => (
          <div key={k} className="flex flex-col">
            <span className="uppercase tracking-wider text-[10px] text-muted-foreground">{k}</span>
            <span className="font-medium">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create `frontend/components/section-block.tsx`**

```tsx
"use client";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import type { DeltaSection } from "@/lib/types";
import { CitationBadge } from "./citation-badge";

export function SectionBlock({ delta }: { delta: DeltaSection }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {delta.title}
      </h3>
      <div className="prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
          {delta.markdown}
        </ReactMarkdown>
      </div>
      {delta.citations.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {delta.citations.map((c, i) => (
            <CitationBadge key={i} citation={c} />
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 6: Create `frontend/components/recommendation-card.tsx`**

```tsx
"use client";
import type { DeltaRecommendation } from "@/lib/types";

const SIGNAL_LABEL: Record<DeltaRecommendation["signal"], string> = {
  tactical_buy: "Tactical Buy",
  accumulate: "Accumulate",
  hold: "Hold",
  reduce: "Reduce",
};

export function RecommendationCard({ delta }: { delta: DeltaRecommendation }) {
  const [low, high] = delta.position_size_range;
  return (
    <div className="rounded-lg border-2 border-amber-500/50 bg-amber-500/5 p-4 flex flex-col gap-2">
      <div className="text-sm font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
        {SIGNAL_LABEL[delta.signal]}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <Stat label="Position" value={`${low}–${high}%`} />
        <Stat label="Entry" value={delta.entry_zone} />
        <Stat label="Stop" value={delta.stop} />
        <Stat label="12-mo target" value={delta.target_12mo_base} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="uppercase tracking-wider text-[10px] text-muted-foreground">
        {label}
      </span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}
```

- [ ] **Step 7: Create `frontend/components/disclaimer.tsx`**

```tsx
"use client";
import type { DeltaDisclaimer } from "@/lib/types";

export function Disclaimer({ delta }: { delta: DeltaDisclaimer }) {
  return (
    <p className="text-[11px] italic text-muted-foreground border-t pt-2">
      {delta.text}
    </p>
  );
}
```

- [ ] **Step 8: Create `frontend/components/progress-strip.tsx`**

```tsx
"use client";

const LABELS: Record<string, string> = {
  resolving_ticker: "Resolving ticker",
  running_fundamentals: "Running Fundamental Analyst",
  synthesizing: "Synthesizing",
  received: "Received",
};

export function ProgressStrip({ steps }: { steps: { step: string; ticker?: string }[] }) {
  if (steps.length === 0) return null;
  const last = steps[steps.length - 1];
  return (
    <div className="text-xs text-muted-foreground italic">
      {LABELS[last.step] ?? last.step}
      {last.ticker ? ` · ${last.ticker}` : ""}…
    </div>
  );
}
```

- [ ] **Step 9: Replace `frontend/components/chat-thread.tsx`**

```tsx
"use client";
import { CitationBadge } from "./citation-badge";
import { Disclaimer } from "./disclaimer";
import { ProgressStrip } from "./progress-strip";
import { RecommendationCard } from "./recommendation-card";
import { SectionBlock } from "./section-block";
import { StockCard } from "./stock-card";
import type { ChatMessage, ChatMessageBlock, DeltaQuickTake } from "@/lib/types";

const SIGNAL_BG: Record<DeltaQuickTake["signal"], string> = {
  tactical_buy: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  accumulate: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  hold: "bg-muted",
  reduce: "bg-rose-500/10 text-rose-700 dark:text-rose-400",
};

function renderBlock(block: ChatMessageBlock, i: number): React.ReactNode {
  switch (block.type) {
    case "text":
      return <p key={i}>{block.text}</p>;
    case "quick_take":
      return (
        <div key={i} className={`rounded px-3 py-2 text-sm font-medium ${SIGNAL_BG[block.signal]}`}>
          {block.signal.replace("_", " ")} · {block.qualifier}
        </div>
      );
    case "stock_card":
      return <StockCard key={i} delta={block} />;
    case "section":
      return <SectionBlock key={i} delta={block} />;
    case "recommendation":
      return <RecommendationCard key={i} delta={block} />;
    case "disclaimer":
      return <Disclaimer key={i} delta={block} />;
    default:
      return null;
  }
}

export function ChatThread({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex flex-col gap-6 p-6">
      {messages.map((m) => (
        <div
          key={m.id}
          className={m.role === "user" ? "self-end max-w-2xl" : "self-stretch max-w-3xl"}
        >
          {m.role === "user" ? (
            <div className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
              {Array.isArray(m.content) ? null
                : m.content.type === "text" ? <p>{m.content.text}</p>
                : null}
            </div>
          ) : (
            <div className="flex flex-col gap-4 rounded-lg bg-muted/30 px-4 py-3">
              {m.progress && m.progress.length > 0 && <ProgressStrip steps={m.progress} />}
              {Array.isArray(m.content)
                ? m.content.map((b, i) => renderBlock(b, i))
                : m.content.type === "text"
                  ? <p>{m.content.text}</p>
                  : null}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// re-exported so tests can import the badge from the thread module path too
export { CitationBadge };
```

- [ ] **Step 10: Update `frontend/app/chat/page.tsx` to handle all delta types**

Replace the `handleSend` body's delta-handling loop with one that switches on `delta.type` for everything (not just text). The full new file:

```tsx
"use client";
import { useEffect, useState } from "react";

import { ChatThread } from "@/components/chat-thread";
import { Composer } from "@/components/composer";
import { parseSSEStream, postChatStream } from "@/lib/sse";
import { createClient } from "@/lib/supabase/client";
import type { ChatMessage, ChatMessageBlock } from "@/lib/types";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [chatId, setChatId] = useState<string | null>(null);
  const [jwt, setJwt] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      setJwt(session?.access_token ?? null);
    });
  }, []);

  async function handleSend(text: string) {
    if (!jwt) return;
    setSending(true);
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: { type: "text", text },
    };
    setMessages((m) => [...m, userMsg]);

    try {
      const stream = await postChatStream({
        jwt, content: text, chatId, backendUrl: BACKEND_URL,
      });
      const asstId = crypto.randomUUID();
      const asstMsg: ChatMessage = {
        id: asstId, role: "assistant", content: [], progress: [],
      };
      setMessages((m) => [...m, asstMsg]);
      const blocks: ChatMessageBlock[] = [];
      const progress: { step: string; ticker?: string }[] = [];

      for await (const ev of parseSSEStream(stream)) {
        if (ev.event === "progress") {
          progress.push({ step: ev.data.step, ticker: ev.data.ticker });
          const progSnapshot = [...progress];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, progress: progSnapshot } : x),
          );
        } else if (ev.event === "delta") {
          blocks.push(ev.data);
          const blocksSnapshot = [...blocks];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, content: blocksSnapshot } : x),
          );
        } else if (ev.event === "done") {
          if (ev.data.chat_id) setChatId(ev.data.chat_id);
        } else if (ev.event === "error") {
          blocks.push({ type: "text", text: `Error: ${ev.data.message}` });
          const blocksSnapshot = [...blocks];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, content: blocksSnapshot } : x),
          );
        }
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h1 className="text-sm font-semibold">Stylobate</h1>
      </header>
      <div className="flex-1 overflow-y-auto">
        <ChatThread messages={messages} />
      </div>
      <Composer onSend={handleSend} disabled={sending || !jwt} />
    </main>
  );
}
```

- [ ] **Step 11: Write component tests**

`frontend/__tests__/delta-renderers.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { CitationBadge } from "@/components/citation-badge";
import { Disclaimer } from "@/components/disclaimer";
import { RecommendationCard } from "@/components/recommendation-card";
import { SectionBlock } from "@/components/section-block";
import { StockCard } from "@/components/stock-card";

describe("delta renderers", () => {
  it("renders stock card with stats", () => {
    render(
      <StockCard delta={{
        type: "stock_card", ticker: "AAPL", name: "Apple Inc.",
        market: "US", currency: "USD",
        stats: { "P/E": "29.5", "Net margin": "25.5%" },
      }} />,
    );
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Apple Inc. · US")).toBeInTheDocument();
    expect(screen.getByText("29.5")).toBeInTheDocument();
    expect(screen.getByText("25.5%")).toBeInTheDocument();
  });

  it("renders section markdown + citation badges", () => {
    render(
      <SectionBlock delta={{
        type: "section", title: "Fundamentals",
        markdown: "Net margin **25.5%**",
        citations: [{ source: "yfinance", ref: "yfinance:ratios:AAPL", index: 1 }],
      }} />,
    );
    expect(screen.getByText("Fundamentals")).toBeInTheDocument();
    expect(screen.getByText(/25\.5/)).toBeInTheDocument();
    expect(screen.getByText("yfinance·1")).toBeInTheDocument();
  });

  it("renders recommendation card with all fields", () => {
    render(
      <RecommendationCard delta={{
        type: "recommendation", signal: "tactical_buy",
        position_size_range: [2, 4], entry_zone: "440-455",
        stop: "385", target_12mo_base: "540",
      }} />,
    );
    expect(screen.getByText("Tactical Buy")).toBeInTheDocument();
    expect(screen.getByText("2–4%")).toBeInTheDocument();
    expect(screen.getByText("440-455")).toBeInTheDocument();
    expect(screen.getByText("385")).toBeInTheDocument();
    expect(screen.getByText("540")).toBeInTheDocument();
  });

  it("renders citation badge with index label", () => {
    render(<CitationBadge citation={{ source: "edgar", ref: "AAPL 10-K", index: 3 }} />);
    expect(screen.getByText("edgar·3")).toBeInTheDocument();
  });

  it("renders disclaimer text", () => {
    render(<Disclaimer delta={{ type: "disclaimer", text: "Educational analysis…" }} />);
    expect(screen.getByText("Educational analysis…")).toBeInTheDocument();
  });
});
```

Also create `frontend/__tests__/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

Update `frontend/vitest.config.ts` to load the setup file:

```ts
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./__tests__/setup.ts"],
    include: ["**/__tests__/**/*.test.{ts,tsx}", "**/*.test.{ts,tsx}"],
  },
});
```

- [ ] **Step 12: Run frontend tests**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm test
```

Expected: all tests pass (existing smoke + sse + 5 new delta-renderer tests).

- [ ] **Step 13: Lint + build**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm run lint && npm run build
```

Expected: clean.

- [ ] **Step 14: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add frontend/lib/types.ts frontend/components frontend/app/chat/page.tsx \
        frontend/__tests__ frontend/vitest.config.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): render Phase 1 typed deltas (stock card, sections, reco, disclaimer)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: End-to-end live test on AAPL

**Files:** none — verification only.

**Goal:** Confirm the deployed (or local) pipeline actually produces a real research note for AAPL against the live Anthropic API + live yfinance + live EDGAR. This is the Phase 1 success criterion.

- [ ] **Step 1: Ensure dev servers are up**

```bash
cd /Users/rakhisinha/Stylobate
# Backend
make backend &
# Frontend
make frontend &
```

Wait ~5 seconds for both to start. Verify:

```bash
curl -s http://localhost:8000/healthz
```

Expected: `{"status":"ok","service":"stylobate-backend"}`.

- [ ] **Step 2: Get a fresh user JWT**

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
SIGNIN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}')
TOKEN=$(echo "$SIGNIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "$TOKEN" > /tmp/stylobate_token.txt
test -n "$TOKEN" && echo "got token" || { echo "FAILED"; exit 1; }
```

- [ ] **Step 3: Run a real deep-dive on AAPL**

```bash
TOKEN=$(cat /tmp/stylobate_token.txt)
curl -s -X POST "http://localhost:8000/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"deep dive on AAPL"}' \
  --max-time 60 > /tmp/aapl_stream.txt
echo "=== stream length:" $(wc -l < /tmp/aapl_stream.txt) "lines ==="
grep -E "^(event:|data:)" /tmp/aapl_stream.txt | head -40
```

**Expected output:**
- `event: progress` frames for `resolving_ticker`, `running_fundamentals`, `synthesizing`
- `event: delta` frames for: `quick_take`, `stock_card` (with real AAPL stats), 3+ `section`s (Thesis, Fundamentals, Risks with markdown + citations), `recommendation`, `disclaimer`
- `event: done` at the end with a `message_id`

If you see `event: error` with "couldn't identify the ticker", the resolver returned low confidence — retry with explicit "AAPL" in the query.

- [ ] **Step 4: Verify DB rows**

```bash
# Via the Supabase MCP tool already configured for the controller, or via the Dashboard
# SQL Editor at https://supabase.com/dashboard/project/pvjamgocmmldfpzzswaj/sql:
#
#   select role, jsonb_pretty(content)
#   from messages
#   where chat_id = (
#     select id from chats
#     where user_id = 'e94fd399-9578-4b85-807b-1aaf1fdd4ec5'
#     order by last_message_at desc limit 1
#   )
#   order by created_at;
#
# Expected: a user "deep dive on AAPL" row + an assistant row whose content is an array
# of typed blocks (quick_take, stock_card, section, section, section, recommendation, disclaimer).
```

- [ ] **Step 5: Verify in browser**

Open https://stylobate.vercel.app/chat (or http://localhost:3000/chat). Sign in. Type "deep dive on AAPL". Watch:

- A progress strip appears under your message ("Resolving ticker…" → "Running Fundamental Analyst…" → "Synthesizing…")
- A green "tactical buy / hold / reduce" pill appears
- A stock card with AAPL identity + stats
- Three section blocks (Thesis, Fundamentals, Risks)
- A gold-bordered recommendation card
- A small grey disclaimer italic line at the bottom

If anything is missing, check both the backend log (`tail -30 /private/tmp/claude-501/.../tasks/...output`) and the `tool_calls` table in Supabase for which agent stopped.

- [ ] **Step 6: Stop dev servers**

```bash
pkill -f "uvicorn app.main"
pkill -f "next dev"
```

- [ ] **Step 7: Final Phase 1 commit if you've made any small touch-up tweaks during smoke testing**

```bash
cd /Users/rakhisinha/Stylobate
git status
# Only commit if there are intentional changes from smoke-testing.
git diff
```

Otherwise nothing to commit; Phase 1 ends with all the previous tasks already committed.

---

## End-of-Phase checklist

- [ ] All backend tests green (`uv run pytest -q`, expected ~25 passing total)
- [ ] All frontend tests green (`npm test`, expected ~8 passing)
- [ ] mypy strict clean on backend
- [ ] ruff clean on backend
- [ ] `npm run lint` clean on frontend
- [ ] `npm run build` succeeds
- [ ] Live AAPL deep-dive produces all expected delta types with real data
- [ ] Each section with numeric claims has at least one citation badge
- [ ] Recommendation card uses an allowed signal (`tactical_buy` / `accumulate` / `hold` / `reduce`)
- [ ] Educational disclaimer appears on every assistant response
- [ ] Frontend pushed to Vercel and the new pipeline works on the live URL (only if backend is publicly reachable; otherwise Phase 1 deploys backend separately or stays local)

After Phase 1 lands, the next plan to write is Phase 2 — add Technical, News, Macro specialists + dispatch tool for parallel orchestration + Indian data sources.
