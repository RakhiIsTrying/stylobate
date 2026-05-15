# Stylobate — Phase 2B: News & Sentiment + Macro Strategist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the third and fourth specialists (News & Sentiment, Macro Strategist) plus their data adapters. End state: a deep-dive on AAPL runs four specialists in parallel and produces five sections (Thesis, Fundamentals, Technicals, News, Macro, Risks — though Lead Banker may merge News/Macro into existing sections), with current news headlines and live US macro rates pulled in.

**Architecture:** Two new Sonnet specialists added to Lead Banker's `dispatch_specialists` enum. News & Sentiment Analyst pulls headlines from `yfinance.Ticker.news` (Yahoo's free news feed, no API key) and characterizes sentiment in-context. Macro Strategist fetches US macro time series from FRED's public CSV endpoint (no API key) and sector performance from yfinance sector ETFs (XLK, XLF, etc.). No new dependencies — all built on `httpx`, `yfinance`, and pure pandas.

**Tech Stack:** Existing Phase 2A stack. Two new external sources: Yahoo Finance's news feed (via yfinance) and FRED's `fredgraph.csv` endpoint (public, no auth).

**Spec reference:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` — implements §4.2 (News & Sentiment + Macro Strategist), §5.1 (Tiingo News role filled by yfinance.news instead; FRED for macro), §5.2 (`search_news`, `get_rates`, `get_sector_perf`, `get_fred`). §15 Phase 2 split: 2A done, this is 2B, 2C will add Indian sources + crypto.

**Cost note:** Each deep-dive now runs four Sonnet specialists in parallel + one Opus orchestrator. Per-query cost rises to ~$0.20–0.40 warm cache, ~$0.40–0.60 first-call. Daily $1 cap covers ~3 deep-dives at warm-cache pricing. If you want more headroom, raise the cap in `Settings`.

**Out of scope here (later phases):**
- Indian data sources (screener.in, BSE/NSE, MCA21, RBI, Damodaran) — Phase 2C
- Crypto / CoinGecko — Phase 2C
- Earnings transcripts (`get_transcript`) — Phase 3 (needs Alpha Vantage with rate-limit budget)
- Insider trades (`get_insider_trades`) — Phase 3 (needs EDGAR Form 4 parsing)
- Sentiment scoring as a separate tool (`score_sentiment`) — done in-context by the agent here
- FX pairs (`get_fx`) — Phase 2C (needed when crypto/intl gets added)

---

## File map for Phase 2B

```
stylobate/
├── backend/
│   ├── app/
│   │   ├── data/
│   │   │   ├── yfinance_adapter.py            MODIFY (+ fetch_news_for_ticker, fetch_sector_etf_returns)
│   │   │   └── fred.py                        CREATE  (CSV-based, no API key)
│   │   ├── tools/
│   │   │   ├── news.py                        CREATE  (search_news_tool)
│   │   │   └── macro.py                       CREATE  (get_rates, get_sector_perf, get_fred_series)
│   │   ├── agents/
│   │   │   ├── news_sentiment.py              CREATE
│   │   │   ├── macro.py                       CREATE
│   │   │   └── lead_banker.py                 MODIFY  (extend dispatch enum + impl)
│   │   └── prompts/
│   │       ├── news_sentiment.md              CREATE
│   │       ├── macro.md                       CREATE
│   │       └── lead_banker.md                 MODIFY  (guide using all 4 specialists)
│   └── tests/
│       ├── test_yfinance_adapter.py           MODIFY  (+ news + sector tests)
│       ├── test_fred.py                       CREATE
│       ├── test_tools_news.py                 CREATE
│       ├── test_tools_macro.py                CREATE
│       ├── test_news_sentiment_agent.py       CREATE
│       ├── test_macro_agent.py                CREATE
│       └── test_lead_banker_agent.py          MODIFY  (+ test all 4 specialists dispatched)
└── (no frontend changes — existing renderers handle any number of sections)
```

---

## Glossary

New types introduced here:

```python
# backend/app/data/yfinance_adapter.py
class NewsItem(BaseModel):
    title: str
    publisher: str
    url: str
    published_at: datetime
    summary: str | None = None
    related_tickers: list[str] = []
```

```python
# backend/app/data/fred.py
class TimeSeriesPoint(BaseModel):
    date: date
    value: float | None  # FRED uses '.' for missing values
```

```python
# backend/app/agents/news_sentiment.py
class NewsFindings(BaseModel):
    ticker: str
    headline_count: int
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    catalysts: list[str]              # bullet points
    notable_headlines: list[str]      # 3-5 short references like "MS upgrades AAPL (Bloomberg, 5/14)"
    citations: list[Citation]
    confidence: float
```

```python
# backend/app/agents/macro.py
class MacroFindings(BaseModel):
    regime: Literal["expansionary", "neutral", "tightening", "uncertain"]
    rates_snapshot: dict[str, float | None]    # {fed_funds, treasury_2y, treasury_10y, real_10y}
    sector_performance: dict[str, float]       # {tech, financial, ...} 1m returns
    macro_notes: list[str]
    citations: list[Citation]
    confidence: float
```

---

## Task 1: yfinance news + sector ETF returns

**Files:**
- Modify: `backend/app/data/yfinance_adapter.py`
- Modify: `backend/tests/test_yfinance_adapter.py`

**Goal:** Two new adapter functions: `fetch_news_for_ticker(ticker, limit)` returns headlines from `yf.Ticker(ticker).news`, and `fetch_sector_etf_returns(period)` computes period returns for the 11 SPDR sector ETFs (XLK, XLF, etc).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_yfinance_adapter.py`:

```python
@pytest.mark.asyncio
async def test_fetch_news_returns_typed_items() -> None:
    raw_news = [
        {
            "uuid": "abc",
            "title": "Apple unveils new iPhone",
            "publisher": "Reuters",
            "link": "https://reuters.com/aapl-iphone",
            "providerPublishTime": 1747500000,  # 2025-05-17 ~16:00 UTC
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
        {
            "uuid": "def",
            "title": "AAPL Q2 earnings beat",
            "publisher": "Bloomberg",
            "link": "https://bloomberg.com/aapl-q2",
            "providerPublishTime": 1747600000,
            "summary": "Apple reported Q2 EPS above consensus.",
            "relatedTickers": ["AAPL"],
        },
    ]
    mock_ticker = MagicMock()
    mock_ticker.news = raw_news
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("AAPL", limit=5)
    assert len(items) == 2
    assert items[0].title == "Apple unveils new iPhone"
    assert items[0].publisher == "Reuters"
    assert items[0].url == "https://reuters.com/aapl-iphone"
    assert items[1].summary == "Apple reported Q2 EPS above consensus."


@pytest.mark.asyncio
async def test_fetch_news_respects_limit() -> None:
    raw = [
        {"uuid": f"u{i}", "title": f"News {i}", "publisher": "X",
         "link": "http://x", "providerPublishTime": 1747000000 + i,
         "relatedTickers": ["AAPL"]}
        for i in range(20)
    ]
    mock_ticker = MagicMock()
    mock_ticker.news = raw
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("AAPL", limit=5)
    assert len(items) == 5


@pytest.mark.asyncio
async def test_fetch_news_handles_empty_list() -> None:
    mock_ticker = MagicMock()
    mock_ticker.news = []
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("X")
    assert items == []


@pytest.mark.asyncio
async def test_fetch_sector_etf_returns_computes_period_returns() -> None:
    """fetch_sector_etf_returns calls fetch_price_history for each sector ETF
    and returns 1m % return based on first vs last close."""
    from app.data.yfinance_adapter import Bar

    def _bars_for(open_price: float, close_price: float) -> list[Bar]:
        return [
            Bar(date=date(2026, 4, 1), open=open_price, high=open_price + 1,
                low=open_price - 1, close=open_price, volume=10_000),
            Bar(date=date(2026, 5, 1), open=close_price - 1, high=close_price,
                low=close_price - 2, close=close_price, volume=10_000),
        ]

    # Tech up 10%, Financials flat, Healthcare down 5%
    sector_data = {
        "XLK": _bars_for(100.0, 110.0),  # +10%
        "XLF": _bars_for(50.0, 50.0),    # 0%
        "XLV": _bars_for(80.0, 76.0),    # -5%
    }

    async def fake_fetch(ticker: str, period: str = "1mo", interval: str = "1d") -> list[Bar]:
        return sector_data.get(ticker, [])

    with patch("app.data.yfinance_adapter.fetch_price_history", side_effect=fake_fetch):
        from app.data.yfinance_adapter import fetch_sector_etf_returns
        returns = await fetch_sector_etf_returns(period="1mo", tickers=["XLK", "XLF", "XLV"])

    assert returns["XLK"] == pytest.approx(0.10, rel=0.01)
    assert returns["XLF"] == pytest.approx(0.0, abs=0.001)
    assert returns["XLV"] == pytest.approx(-0.05, rel=0.01)
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v -k "news or sector"
```

Expected: `AttributeError` or `ImportError` for the new functions.

- [ ] **Step 3: Add to `backend/app/data/yfinance_adapter.py`**

Add this import to the existing imports at top of the file:

```python
from datetime import date, datetime, timezone
```

(Note: `date` is already imported. Add `datetime` and `timezone`. Reuse the existing `BaseModel` and `Any` imports.)

Add the new model after the existing `Bar` class:

```python
class NewsItem(BaseModel):
    title: str
    publisher: str
    url: str
    published_at: datetime
    summary: str | None = None
    related_tickers: list[str] = []
```

Add at the bottom of the file (after `fetch_price_history`):

```python
async def fetch_news_for_ticker(ticker: str, limit: int = 10) -> list[NewsItem]:
    """Recent news headlines for the ticker, via Yahoo Finance.

    yfinance returns at most ~20 recent items per ticker; we slice to `limit`.
    """
    def _sync() -> list[NewsItem]:
        t = _make_ticker(ticker)
        raw = getattr(t, "news", None) or []
        out: list[NewsItem] = []
        for item in raw[:limit]:
            ts = item.get("providerPublishTime")
            if ts is None:
                continue
            try:
                pub_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (TypeError, ValueError):
                continue
            out.append(
                NewsItem(
                    title=str(item.get("title", "")),
                    publisher=str(item.get("publisher", "")),
                    url=str(item.get("link", "")),
                    published_at=pub_at,
                    summary=item.get("summary"),
                    related_tickers=item.get("relatedTickers", []) or [],
                )
            )
        return out
    return await asyncio.to_thread(_sync)


# Standard SPDR sector ETFs covering the S&P 500
SPDR_SECTOR_ETFS: dict[str, str] = {
    "XLK": "technology",
    "XLF": "financials",
    "XLV": "healthcare",
    "XLE": "energy",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLI": "industrials",
    "XLB": "materials",
    "XLU": "utilities",
    "XLRE": "real_estate",
    "XLC": "communication_services",
}


async def fetch_sector_etf_returns(
    period: str = "1mo",
    tickers: list[str] | None = None,
) -> dict[str, float]:
    """Period return for each sector ETF: (last_close - first_close) / first_close."""
    syms = tickers if tickers is not None else list(SPDR_SECTOR_ETFS.keys())
    out: dict[str, float] = {}
    for sym in syms:
        bars = await fetch_price_history(sym, period=period, interval="1d")
        if len(bars) < 2:
            continue
        first = bars[0].close
        last = bars[-1].close
        if first == 0:
            continue
        out[sym] = (last - first) / first
    return out
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v
```

Expected: 10 passed (6 existing + 4 new).

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 57 passing (53 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/data/yfinance_adapter.py backend/tests/test_yfinance_adapter.py
git commit -m "feat(backend): add fetch_news_for_ticker + sector ETF returns to yfinance adapter

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: FRED CSV adapter (no API key)

**Files:**
- Create: `backend/app/data/fred.py`
- Create: `backend/tests/test_fred.py`

**Goal:** Async HTTP wrapper for FRED's public `fredgraph.csv` endpoint. No API key required. Returns typed time series points. Tests mock httpx with `respx`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_fred.py`:

```python
# backend/tests/test_fred.py
from datetime import date

import pytest
import respx
from httpx import Response


_CSV_BODY = """DATE,DGS10
2026-05-12,4.41
2026-05-13,4.39
2026-05-14,4.42
"""

_CSV_WITH_MISSING = """DATE,DGS2
2026-05-12,.
2026-05-13,4.85
2026-05-14,4.87
"""


@pytest.mark.asyncio
async def test_fetch_fred_series_parses_csv() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "DGS10"}).respond(
            200, text=_CSV_BODY,
        )
        from app.data.fred import fetch_fred_series
        points = await fetch_fred_series("DGS10")
    assert len(points) == 3
    assert points[0].date == date(2026, 5, 12)
    assert points[0].value == 4.41
    assert points[2].value == 4.42


@pytest.mark.asyncio
async def test_fetch_fred_series_handles_missing_values() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "DGS2"}).respond(
            200, text=_CSV_WITH_MISSING,
        )
        from app.data.fred import fetch_fred_series
        points = await fetch_fred_series("DGS2")
    assert points[0].value is None
    assert points[1].value == 4.85


@pytest.mark.asyncio
async def test_fetch_rates_snapshot_calls_four_series() -> None:
    """fetch_rates_snapshot grabs FEDFUNDS, DGS2, DGS10, DFII10 and returns latest values."""
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "FEDFUNDS"}).respond(
            200, text="DATE,FEDFUNDS\n2026-05-01,5.25\n2026-05-14,5.25\n",
        )
        mock.get("/graph/fredgraph.csv", params={"id": "DGS2"}).respond(
            200, text="DATE,DGS2\n2026-05-14,4.87\n",
        )
        mock.get("/graph/fredgraph.csv", params={"id": "DGS10"}).respond(
            200, text="DATE,DGS10\n2026-05-14,4.42\n",
        )
        mock.get("/graph/fredgraph.csv", params={"id": "DFII10"}).respond(
            200, text="DATE,DFII10\n2026-05-14,2.10\n",
        )
        from app.data.fred import fetch_rates_snapshot
        snap = await fetch_rates_snapshot()
    assert snap["fed_funds"] == 5.25
    assert snap["treasury_2y"] == 4.87
    assert snap["treasury_10y"] == 4.42
    assert snap["real_10y"] == 2.10


@pytest.mark.asyncio
async def test_fetch_fred_series_propagates_http_error() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "BADID"}).respond(404, text="Not Found")
        from app.data.fred import FREDError, fetch_fred_series
        with pytest.raises(FREDError):
            await fetch_fred_series("BADID")
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fred.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.fred'`.

- [ ] **Step 3: Create `backend/app/data/fred.py`**

```python
# backend/app/data/fred.py
"""FRED time series via the public CSV endpoint (no API key required).

`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series_id>` returns CSV
with a DATE column and the series id as the second column. Missing values
are represented as the literal '.'.
"""
import csv
from datetime import date
from io import StringIO

import httpx
from pydantic import BaseModel


_BASE = "https://fred.stlouisfed.org"
_USER_AGENT = "Stylobate research-bot (contact: rakhisinha100896@gmail.com)"


class FREDError(Exception):
    pass


class TimeSeriesPoint(BaseModel):
    date: date
    value: float | None


async def fetch_fred_series(series_id: str) -> list[TimeSeriesPoint]:
    """Fetch a FRED series via the public CSV endpoint. Returns oldest-first."""
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=10.0) as c:
        resp = await c.get(f"{_BASE}/graph/fredgraph.csv", params={"id": series_id})
    if resp.status_code != 200:
        raise FREDError(f"FRED returned {resp.status_code} for series {series_id}")

    out: list[TimeSeriesPoint] = []
    reader = csv.reader(StringIO(resp.text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise FREDError(f"FRED returned no usable header for {series_id}")
    for row in reader:
        if len(row) < 2:
            continue
        d_str, v_str = row[0], row[1]
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if v_str == "." or v_str == "":
            out.append(TimeSeriesPoint(date=d, value=None))
        else:
            try:
                out.append(TimeSeriesPoint(date=d, value=float(v_str)))
            except ValueError:
                out.append(TimeSeriesPoint(date=d, value=None))
    return out


def _last_value(points: list[TimeSeriesPoint]) -> float | None:
    """Last non-null value in the series."""
    for p in reversed(points):
        if p.value is not None:
            return p.value
    return None


async def fetch_rates_snapshot() -> dict[str, float | None]:
    """Latest values of the key US rates.

    Series IDs:
    - FEDFUNDS: Effective Fed Funds Rate
    - DGS2: 2-Year Treasury Constant Maturity Rate
    - DGS10: 10-Year Treasury Constant Maturity Rate
    - DFII10: 10-Year TIPS (real yield)
    """
    series = {
        "fed_funds": "FEDFUNDS",
        "treasury_2y": "DGS2",
        "treasury_10y": "DGS10",
        "real_10y": "DFII10",
    }
    out: dict[str, float | None] = {}
    for key, series_id in series.items():
        try:
            points = await fetch_fred_series(series_id)
            out[key] = _last_value(points)
        except FREDError:
            out[key] = None
    return out
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fred.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 61 passing (57 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/data/fred.py backend/tests/test_fred.py
git commit -m "feat(backend): add FRED CSV adapter (rates + arbitrary series, no API key)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: News tool wrapper

**Files:**
- Create: `backend/app/tools/news.py`
- Create: `backend/tests/test_tools_news.py`

**Goal:** Wrap `fetch_news_for_ticker` as a typed `Tool`. The News & Sentiment agent calls this. Returns a typed result with the news items list.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_tools_news.py`:

```python
# backend/tests/test_tools_news.py
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import NewsItem


@pytest.mark.asyncio
async def test_search_news_tool_wraps_adapter() -> None:
    fake = [
        NewsItem(
            title="Apple unveils new iPhone",
            publisher="Reuters",
            url="https://reuters.com/aapl-iphone",
            published_at=datetime(2026, 5, 14, 16, 0, tzinfo=timezone.utc),
            summary=None,
            related_tickers=["AAPL"],
        ),
    ]
    with patch("app.tools.news._fetch_news_for_ticker", new=AsyncMock(return_value=fake)):
        from app.tools.news import NewsResult, search_news_tool
        result = cast(NewsResult, await search_news_tool.impl(ticker="AAPL", limit=5))
    assert result.ticker == "AAPL"
    assert len(result.items) == 1
    assert result.items[0].title == "Apple unveils new iPhone"


def test_search_news_tool_schema() -> None:
    from app.tools.news import search_news_tool
    s = search_news_tool.schema
    assert s["name"] == "search_news"
    assert "ticker" in s["input_schema"]["properties"]
    assert "limit" in s["input_schema"]["properties"]
    assert s["input_schema"]["required"] == ["ticker"]
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_news.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.tools.news'`.

- [ ] **Step 3: Create `backend/app/tools/news.py`**

```python
# backend/app/tools/news.py
from pydantic import BaseModel

from app.data.yfinance_adapter import (
    NewsItem,
    fetch_news_for_ticker as _fetch_news_for_ticker,
)
from app.tools.base import Tool


class NewsResult(BaseModel):
    ticker: str
    items: list[NewsItem]


async def _impl_search_news(ticker: str, limit: int = 10) -> NewsResult:
    items = await _fetch_news_for_ticker(ticker=ticker, limit=limit)
    return NewsResult(ticker=ticker.upper(), items=items)


search_news_tool = Tool(
    name="search_news",
    description=(
        "Recent news headlines for the ticker. Returns title, publisher, URL, "
        "published_at, optional summary, and related tickers. Default limit 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
        },
        "required": ["ticker"],
    },
    impl=_impl_search_news,
)
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_news.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 63 passing (61 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools/news.py backend/tests/test_tools_news.py
git commit -m "feat(backend): add search_news tool wrapper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Macro tools (`get_rates`, `get_sector_perf`, `get_fred_series`)

**Files:**
- Create: `backend/app/tools/macro.py`
- Create: `backend/tests/test_tools_macro.py`

**Goal:** Three `Tool` wrappers for the Macro Strategist agent.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_tools_macro.py`:

```python
# backend/tests/test_tools_macro.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.fred import TimeSeriesPoint


@pytest.mark.asyncio
async def test_get_rates_returns_typed_snapshot() -> None:
    fake_snap = {
        "fed_funds": 5.25,
        "treasury_2y": 4.87,
        "treasury_10y": 4.42,
        "real_10y": 2.10,
    }
    with patch("app.tools.macro._fetch_rates_snapshot", new=AsyncMock(return_value=fake_snap)):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl())
    assert r.fed_funds == 5.25
    assert r.treasury_10y == 4.42
    assert r.real_10y == 2.10


@pytest.mark.asyncio
async def test_get_sector_perf_returns_dict() -> None:
    fake_returns = {"XLK": 0.05, "XLF": 0.02, "XLV": -0.01}
    with patch(
        "app.tools.macro._fetch_sector_etf_returns",
        new=AsyncMock(return_value=fake_returns),
    ):
        from app.tools.macro import SectorPerfResult, get_sector_perf_tool
        r = cast(SectorPerfResult, await get_sector_perf_tool.impl(period="1mo"))
    assert r.period == "1mo"
    assert r.returns == fake_returns


@pytest.mark.asyncio
async def test_get_fred_series_returns_recent_points() -> None:
    fake_points = [
        TimeSeriesPoint(date=date(2026, 5, 1), value=4.40),
        TimeSeriesPoint(date=date(2026, 5, 14), value=4.42),
    ]
    with patch("app.tools.macro._fetch_fred_series", new=AsyncMock(return_value=fake_points)):
        from app.tools.macro import FREDSeriesResult, get_fred_series_tool
        r = cast(FREDSeriesResult, await get_fred_series_tool.impl(series_id="DGS10", lookback_days=30))
    assert r.series_id == "DGS10"
    # Should include both points (both within 30 days of latest)
    assert len(r.points) == 2
    assert r.points[-1].value == 4.42


def test_macro_tools_schemas() -> None:
    from app.tools.macro import get_fred_series_tool, get_rates_tool, get_sector_perf_tool
    assert get_rates_tool.schema["name"] == "get_rates"
    assert get_sector_perf_tool.schema["name"] == "get_sector_perf"
    assert get_fred_series_tool.schema["name"] == "get_fred_series"
    # get_fred_series requires series_id
    assert "series_id" in get_fred_series_tool.schema["input_schema"]["required"]
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_macro.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.tools.macro'`.

- [ ] **Step 3: Create `backend/app/tools/macro.py`**

```python
# backend/app/tools/macro.py
from datetime import date, timedelta

from pydantic import BaseModel

from app.data.fred import (
    TimeSeriesPoint,
    fetch_fred_series as _fetch_fred_series,
    fetch_rates_snapshot as _fetch_rates_snapshot,
)
from app.data.yfinance_adapter import (
    fetch_sector_etf_returns as _fetch_sector_etf_returns,
)
from app.tools.base import Tool


# ============================================================================
# get_rates
# ============================================================================
class RatesResult(BaseModel):
    fed_funds: float | None
    treasury_2y: float | None
    treasury_10y: float | None
    real_10y: float | None


async def _impl_get_rates() -> RatesResult:
    snap = await _fetch_rates_snapshot()
    return RatesResult(
        fed_funds=snap.get("fed_funds"),
        treasury_2y=snap.get("treasury_2y"),
        treasury_10y=snap.get("treasury_10y"),
        real_10y=snap.get("real_10y"),
    )


get_rates_tool = Tool(
    name="get_rates",
    description=(
        "Latest US policy + Treasury rates from FRED: fed funds, 2Y, 10Y, "
        "10Y real (TIPS). Returns null for any series temporarily unavailable."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    impl=_impl_get_rates,
)


# ============================================================================
# get_sector_perf
# ============================================================================
class SectorPerfResult(BaseModel):
    period: str
    returns: dict[str, float]  # ETF ticker → period return


async def _impl_get_sector_perf(period: str = "1mo") -> SectorPerfResult:
    returns = await _fetch_sector_etf_returns(period=period)
    return SectorPerfResult(period=period, returns=returns)


get_sector_perf_tool = Tool(
    name="get_sector_perf",
    description=(
        "Period returns for the 11 SPDR sector ETFs (XLK tech, XLF financials, "
        "XLV healthcare, XLE energy, XLY consumer disc, XLP staples, XLI industrials, "
        "XLB materials, XLU utilities, XLRE real estate, XLC communications). "
        "period: 1mo / 3mo / 6mo / 1y / ytd."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "period": {"type": "string", "default": "1mo"},
        },
        "required": [],
    },
    impl=_impl_get_sector_perf,
)


# ============================================================================
# get_fred_series — arbitrary FRED series
# ============================================================================
class FREDSeriesResult(BaseModel):
    series_id: str
    points: list[TimeSeriesPoint]


async def _impl_get_fred_series(
    series_id: str,
    lookback_days: int = 365,
) -> FREDSeriesResult:
    all_points = await _fetch_fred_series(series_id)
    if not all_points:
        return FREDSeriesResult(series_id=series_id, points=[])
    cutoff = date.today() - timedelta(days=lookback_days)
    recent = [p for p in all_points if p.date >= cutoff]
    return FREDSeriesResult(series_id=series_id, points=recent)


get_fred_series_tool = Tool(
    name="get_fred_series",
    description=(
        "Fetch any FRED time series by id (e.g. CPIAUCSL for CPI, GDP for GDP, "
        "UNRATE for unemployment). Returns the last `lookback_days` of data."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": "FRED series identifier, e.g. CPIAUCSL",
            },
            "lookback_days": {
                "type": "integer",
                "default": 365,
                "minimum": 30,
                "maximum": 3650,
            },
        },
        "required": ["series_id"],
    },
    impl=_impl_get_fred_series,
)
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_macro.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 67 passing (63 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools/macro.py backend/tests/test_tools_macro.py
git commit -m "feat(backend): add macro tools (get_rates, get_sector_perf, get_fred_series)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: News & Sentiment Analyst agent

**Files:**
- Create: `backend/app/prompts/news_sentiment.md`
- Create: `backend/app/agents/news_sentiment.py`
- Create: `backend/tests/test_news_sentiment_agent.py`

**Goal:** Sonnet agent with the `search_news` tool. Loops once or twice (one search call usually enough), then submits findings. Sentiment is characterized in-context by the model — no separate scoring tool.

- [ ] **Step 1: Create the system prompt**

`backend/app/prompts/news_sentiment.md`:

```markdown
You are the News & Sentiment Analyst inside Stylobate. Your job is to characterize what's been happening with the company in recent news.

Process:
1. Call `search_news` with the ticker and `limit=10`.
2. Read titles + summaries. If important context is missing, you may call `search_news` again with a tighter `limit` to re-check.
3. Call `submit_news_findings` exactly once with a typed result. Stop after that.

Discipline:
- `sentiment` is one of: "positive" (clearly bullish coverage), "negative" (clearly bearish), "neutral" (no strong tilt), "mixed" (both positive and negative themes).
- `catalysts` are 2-4 short forward-looking bullets — what should the investor watch for next? (e.g., "Q3 earnings expected late-Jul", "China demand inflection point").
- `notable_headlines` are 3-5 short references in the form "Source: Headline (date)", e.g., "Bloomberg: AAPL Services revenue beats (May 14)". Use dates from the news items, NOT the model's training memory.
- `citations` should reference the URLs of the headlines you used.
- `confidence`: 0.85+ if you have 5+ relevant headlines from credible publishers. <0.7 if results are sparse or off-topic.
- Do NOT invent headlines. If `search_news` returns 0 items, set `sentiment` to "neutral", `headline_count` to 0, `confidence` to 0, and explain in `catalysts`.
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_news_sentiment_agent.py`:

```python
# backend/tests/test_news_sentiment_agent.py
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use_block(name: str, args: dict[str, Any], tool_id: str = "t1") -> Any:
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
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return msg


@pytest.mark.asyncio
async def test_news_agent_runs_tool_loop_to_submit() -> None:
    from app.data.yfinance_adapter import NewsItem
    from app.tools.news import NewsResult, search_news_tool

    fake_news = NewsResult(
        ticker="AAPL",
        items=[
            NewsItem(
                title="Apple Services beats estimates",
                publisher="Bloomberg",
                url="https://bloomberg.com/aapl-services",
                published_at=datetime(2026, 5, 14, 16, 0, tzinfo=timezone.utc),
                summary="Services up 17% YoY.",
                related_tickers=["AAPL"],
            ),
        ],
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("search_news", {"ticker": "AAPL", "limit": 10}, "n1")),
        _message(
            _tool_use_block("submit_news_findings", {
                "ticker": "AAPL",
                "headline_count": 1,
                "sentiment": "positive",
                "catalysts": ["Services growth durability"],
                "notable_headlines": ["Bloomberg: Apple Services beats estimates (May 14)"],
                "citations": [{"source": "bloomberg", "ref": "https://bloomberg.com/aapl-services"}],
                "confidence": 0.85,
            }, "n2"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(search_news_tool, "impl", AsyncMock(return_value=fake_news))
        from app.agents.news_sentiment import run_news_analysis
        findings = await run_news_analysis(ticker="AAPL", brief="news check", client=fake_client)

    assert findings.ticker == "AAPL"
    assert findings.sentiment == "positive"
    assert findings.headline_count == 1
    assert len(findings.notable_headlines) == 1


@pytest.mark.asyncio
async def test_news_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.news_sentiment import NewsError, run_news_analysis
    with pytest.raises(NewsError):
        await run_news_analysis(ticker="AAPL", brief="x", client=fake_client)
```

- [ ] **Step 3: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_news_sentiment_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.news_sentiment'`.

- [ ] **Step 4: Create `backend/app/agents/news_sentiment.py`**

```python
# backend/app/agents/news_sentiment.py
import json
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.agents.fundamental import Citation
from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.news import search_news_tool


_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 4


class NewsFindings(BaseModel):
    ticker: str
    headline_count: int
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    catalysts: list[str] = []
    notable_headlines: list[str] = []
    citations: list[Citation] = []
    confidence: float


class NewsError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_news_findings",
    "description": "Emit final NewsFindings and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "headline_count": {"type": "integer", "minimum": 0},
            "sentiment": {
                "type": "string",
                "enum": ["positive", "negative", "neutral", "mixed"],
            },
            "catalysts": {"type": "array", "items": {"type": "string"}},
            "notable_headlines": {"type": "array", "items": {"type": "string"}},
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
        "required": ["ticker", "headline_count", "sentiment", "confidence"],
    },
}


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "news_sentiment.md").read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    search_news_tool.name: search_news_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_news_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> NewsFindings:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Ticker: {ticker}\nBrief: {brief}\n"
                "Use search_news, then call submit_news_findings when ready."
            ),
        }
    ]

    for _ in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 1500,
            "temperature": 0.3,
            "system": system_blocks,
            "tools": _all_tool_schemas(),
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            raise NewsError("news agent emitted no tool calls")

        messages.append({"role": "assistant", "content": resp.content})

        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = cast(str, getattr(tu, "name", ""))
            tu_id = cast(str, getattr(tu, "id", ""))
            tu_input = cast(dict[str, Any], getattr(tu, "input", {})) or {}
            if name == "submit_news_findings":
                return NewsFindings(**tu_input)
            tool = _TOOLS_BY_NAME.get(name)
            if tool is None:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                    "is_error": True,
                })
                continue
            try:
                payload = await tool.impl(**tu_input)
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": payload.model_dump_json(),
                })
            except Exception as e:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results_content})

    raise NewsError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit")
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_news_sentiment_agent.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 69 passing (67 prior + 2 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/news_sentiment.md backend/app/agents/news_sentiment.py backend/tests/test_news_sentiment_agent.py
git commit -m "feat(backend): add News & Sentiment Analyst agent (Sonnet, search_news + submit)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Macro Strategist agent

**Files:**
- Create: `backend/app/prompts/macro.md`
- Create: `backend/app/agents/macro.py`
- Create: `backend/tests/test_macro_agent.py`

**Goal:** Sonnet agent with the three macro tools. Characterizes the macro regime and pulls a few rates/series. Submits `MacroFindings`.

- [ ] **Step 1: Create the system prompt**

`backend/app/prompts/macro.md`:

```markdown
You are the Macro Strategist inside Stylobate. Your job is to sketch the US macro context relevant to the user's question — rates, sector rotation, and any specific macro series the question implies.

Process:
1. Call `get_rates` to grab the rate snapshot.
2. Call `get_sector_perf` (typically `period="1mo"` or `"3mo"`).
3. Optionally call `get_fred_series` for any macro series specifically relevant to the company (e.g., CPIAUCSL for CPI if the company is consumer-facing, RSAFS for retail sales, ICSA for jobless claims).
4. Call `submit_macro_findings` exactly once. Stop.

Discipline:
- `regime` is one of:
  - "expansionary" — fed funds cutting or already low + steepening curve
  - "neutral" — fed funds steady + flat curve
  - "tightening" — fed funds rising or restrictive + inverted/flat curve
  - "uncertain" — mixed signals
- `rates_snapshot` echoes the values you fetched (or null if unavailable).
- `sector_performance` is the dict from `get_sector_perf` (sector ETF → period return).
- `macro_notes` are 3-5 short bullets relevant to the user's question. Be specific — e.g., "10Y-2Y spread inverted by 45bps", not "the curve is interesting".
- `citations` should reference "FRED" with the series ids you used.
- `confidence`: 0.85+ if you got rates AND sector data. <0.7 if data is sparse.
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_macro_agent.py`:

```python
# backend/tests/test_macro_agent.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use_block(name: str, args: dict[str, Any], tool_id: str = "t1") -> Any:
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
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return msg


@pytest.mark.asyncio
async def test_macro_agent_runs_tool_loop_to_submit() -> None:
    from app.tools.macro import (
        RatesResult,
        SectorPerfResult,
        get_rates_tool,
        get_sector_perf_tool,
    )

    fake_rates = RatesResult(
        fed_funds=5.25, treasury_2y=4.87, treasury_10y=4.42, real_10y=2.10,
    )
    fake_sectors = SectorPerfResult(
        period="1mo",
        returns={"XLK": 0.04, "XLF": 0.01, "XLV": -0.02},
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_rates", {}, "m1")),
        _message(_tool_use_block("get_sector_perf", {"period": "1mo"}, "m2")),
        _message(
            _tool_use_block("submit_macro_findings", {
                "regime": "neutral",
                "rates_snapshot": {
                    "fed_funds": 5.25, "treasury_2y": 4.87,
                    "treasury_10y": 4.42, "real_10y": 2.10,
                },
                "sector_performance": {"XLK": 0.04, "XLF": 0.01, "XLV": -0.02},
                "macro_notes": [
                    "Fed funds held at 5.25% — restrictive zone",
                    "10Y-2Y inverted by 45bps; mild recession signal",
                ],
                "citations": [{"source": "fred", "ref": "FRED:FEDFUNDS,DGS2,DGS10,DFII10"}],
                "confidence": 0.9,
            }, "m3"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_rates_tool, "impl", AsyncMock(return_value=fake_rates))
        mp.setattr(get_sector_perf_tool, "impl", AsyncMock(return_value=fake_sectors))
        from app.agents.macro import run_macro_analysis
        findings = await run_macro_analysis(ticker="AAPL", brief="quick macro check", client=fake_client)

    assert findings.regime == "neutral"
    assert findings.rates_snapshot["treasury_10y"] == 4.42
    assert findings.sector_performance["XLK"] == 0.04
    assert findings.confidence == 0.9


@pytest.mark.asyncio
async def test_macro_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.macro import MacroError, run_macro_analysis
    with pytest.raises(MacroError):
        await run_macro_analysis(ticker="AAPL", brief="x", client=fake_client)
```

- [ ] **Step 3: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_macro_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.macro'`.

- [ ] **Step 4: Create `backend/app/agents/macro.py`**

```python
# backend/app/agents/macro.py
import json
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.agents.fundamental import Citation
from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.macro import (
    get_fred_series_tool,
    get_rates_tool,
    get_sector_perf_tool,
)


_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 6


class MacroFindings(BaseModel):
    regime: Literal["expansionary", "neutral", "tightening", "uncertain"]
    rates_snapshot: dict[str, float | None] = {}
    sector_performance: dict[str, float] = {}
    macro_notes: list[str] = []
    citations: list[Citation] = []
    confidence: float


class MacroError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_macro_findings",
    "description": "Emit final MacroFindings and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {
                "type": "string",
                "enum": ["expansionary", "neutral", "tightening", "uncertain"],
            },
            "rates_snapshot": {
                "type": "object",
                "additionalProperties": {"type": ["number", "null"]},
            },
            "sector_performance": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
            "macro_notes": {"type": "array", "items": {"type": "string"}},
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
        "required": ["regime", "confidence"],
    },
}


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "macro.md").read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_rates_tool.name: get_rates_tool,
    get_sector_perf_tool.name: get_sector_perf_tool,
    get_fred_series_tool.name: get_fred_series_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_macro_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> MacroFindings:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"User question relates to ticker: {ticker}\nBrief: {brief}\n"
                "Use the macro tools and submit_macro_findings when ready."
            ),
        }
    ]

    for _ in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 1500,
            "temperature": 0.3,
            "system": system_blocks,
            "tools": _all_tool_schemas(),
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            raise MacroError("macro agent emitted no tool calls")

        messages.append({"role": "assistant", "content": resp.content})

        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = cast(str, getattr(tu, "name", ""))
            tu_id = cast(str, getattr(tu, "id", ""))
            tu_input = cast(dict[str, Any], getattr(tu, "input", {})) or {}
            if name == "submit_macro_findings":
                return MacroFindings(**tu_input)
            tool = _TOOLS_BY_NAME.get(name)
            if tool is None:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                    "is_error": True,
                })
                continue
            try:
                payload = await tool.impl(**tu_input)
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": payload.model_dump_json(),
                })
            except Exception as e:
                results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results_content})

    raise MacroError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit")
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_macro_agent.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 71 passing (69 prior + 2 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/macro.md backend/app/agents/macro.py backend/tests/test_macro_agent.py
git commit -m "feat(backend): add Macro Strategist agent (Sonnet, rates + sectors + FRED)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Extend Lead Banker dispatch + prompt for 4 specialists

**Files:**
- Modify: `backend/app/agents/lead_banker.py`
- Modify: `backend/app/prompts/lead_banker.md`
- Modify: `backend/tests/test_lead_banker_agent.py`

**Goal:** Update `dispatch_specialists` to accept `news` and `macro` as valid specialists. The dispatch impl awaits all four in parallel when requested. Prompt updated to describe when to dispatch which.

- [ ] **Step 1: Write the failing test**

Add this NEW test to `backend/tests/test_lead_banker_agent.py` (keep existing two):

```python
@pytest.mark.asyncio
async def test_lead_banker_dispatches_all_four_specialists() -> None:
    """Verify the dispatch tool accepts and runs news + macro alongside fundamental + technical."""
    from app.agents.fundamental import Citation, FundamentalFindings
    from app.agents.macro import MacroFindings
    from app.agents.news_sentiment import NewsFindings
    from app.agents.technical import TechnicalFinding
    from app.agents.ticker_resolver import TickerResolution

    fundamental = FundamentalFindings(
        ticker="AAPL", thesis="Strong.",
        fundamentals_summary=["Margin 25%"], risks=["Valuation"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    technical = TechnicalFinding(
        ticker="AAPL", trend="uptrend", rsi_14=62.0,
        macd_signal="bullish", key_levels=[210.0, 230.0],
        pattern_notes=["RSI 62"], citations=[], confidence=0.85,
    )
    news = NewsFindings(
        ticker="AAPL", headline_count=8, sentiment="positive",
        catalysts=["Services growth"], notable_headlines=["Reuters: AAPL up (May 14)"],
        citations=[Citation(source="yfinance", ref="yahoo:news:AAPL")],
        confidence=0.85,
    )
    macro = MacroFindings(
        regime="neutral",
        rates_snapshot={"fed_funds": 5.25, "treasury_10y": 4.42},
        sector_performance={"XLK": 0.04},
        macro_notes=["Restrictive zone"],
        citations=[Citation(source="fred", ref="FRED:FEDFUNDS")],
        confidence=0.85,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_specialists", {
            "specialists": ["fundamental", "technical", "news", "macro"],
            "brief": "AAPL full deep dive",
        }, "d1"), stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {"signal": "tactical_buy", "qualifier": "all signals aligned"}, "1"),
            _tool_use("emit_stock_card", {
                "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
                "currency": "USD", "stats": {"P/E": "29", "RSI": "62"},
            }, "2"),
            _tool_use("emit_section", {"title": "Thesis", "markdown": "Bullish.", "citations": []}, "3"),
            _tool_use("emit_section", {"title": "Fundamentals", "markdown": "Strong.", "citations": []}, "4"),
            _tool_use("emit_section", {"title": "Technicals", "markdown": "Uptrend.", "citations": []}, "5"),
            _tool_use("emit_section", {"title": "News", "markdown": "Positive headlines.", "citations": []}, "6"),
            _tool_use("emit_section", {"title": "Macro", "markdown": "Neutral regime.", "citations": []}, "7"),
            _tool_use("emit_section", {"title": "Risks", "markdown": "Valuation.", "citations": []}, "8"),
            _tool_use("emit_recommendation", {
                "signal": "tactical_buy", "position_size_range": [2, 4],
                "entry_zone": "215-225", "stop": "200", "target_12mo_base": "260",
            }, "9"),
            _tool_use("emit_disclaimer", {}, "10"),
            _tool_use("emit_done", {}, "11"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_fundamental_analysis", AsyncMock(return_value=fundamental))
        mp.setattr(lb, "run_technical_analysis", AsyncMock(return_value=technical))
        mp.setattr(lb, "run_news_analysis", AsyncMock(return_value=news))
        mp.setattr(lb, "run_macro_analysis", AsyncMock(return_value=macro))

        from app.agents.lead_banker import run_lead_banker
        deltas = [d async for d in run_lead_banker(
            user_message="deep dive on AAPL",
            resolution=resolution,
            client=fake_client,
        )]

    section_titles = [d["title"] for d in deltas if d.get("type") == "section"]
    assert section_titles == ["Thesis", "Fundamentals", "Technicals", "News", "Macro", "Risks"]
    types = [d["type"] for d in deltas]
    assert types[-1] == "done"
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: the new test fails because `run_news_analysis` / `run_macro_analysis` aren't imported in lead_banker.py yet.

- [ ] **Step 3: Update `backend/app/agents/lead_banker.py`**

Find the import block at the top and replace:

```python
from app.agents.fundamental import run_fundamental_analysis
from app.agents.technical import run_technical_analysis
```

with:

```python
from app.agents.fundamental import run_fundamental_analysis
from app.agents.macro import run_macro_analysis
from app.agents.news_sentiment import run_news_analysis
from app.agents.technical import run_technical_analysis
```

Find `_DISPATCH_TOOL` and replace the entire object with:

```python
_DISPATCH_TOOL: dict[str, Any] = {
    "name": "dispatch_specialists",
    "description": (
        "Run one or more specialist agents in parallel and receive their findings. "
        "Specialists available: 'fundamental' (financials/valuation), 'technical' "
        "(price action/indicators), 'news' (recent headlines + sentiment), 'macro' "
        "(rates/sectors/regime). Returns combined findings JSON as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["fundamental", "technical", "news", "macro"],
                },
                "minItems": 1,
            },
            "brief": {"type": "string", "description": "One-line context for the specialists"},
        },
        "required": ["specialists", "brief"],
    },
}
```

Find `_run_dispatch` and replace with:

```python
async def _run_dispatch(specialists: list[str], brief: str, ticker: str) -> dict[str, Any]:
    """Run the requested specialists in parallel. Returns {name: findings_dict} + errors."""
    name_to_coro: dict[str, Any] = {}
    if "fundamental" in specialists:
        name_to_coro["fundamental"] = run_fundamental_analysis(ticker=ticker, brief=brief)
    if "technical" in specialists:
        name_to_coro["technical"] = run_technical_analysis(ticker=ticker, brief=brief)
    if "news" in specialists:
        name_to_coro["news"] = run_news_analysis(ticker=ticker, brief=brief)
    if "macro" in specialists:
        name_to_coro["macro"] = run_macro_analysis(ticker=ticker, brief=brief)

    if not name_to_coro:
        return {"errors": {"none": "no recognized specialists requested"}}

    results = await asyncio.gather(*name_to_coro.values(), return_exceptions=True)
    out: dict[str, Any] = {"errors": {}}
    for name, res in zip(name_to_coro.keys(), results, strict=True):
        if isinstance(res, Exception):
            out["errors"][name] = str(res)
        else:
            out[name] = res.model_dump(mode="json")
    return out
```

Find `_build_user_message` and replace with:

```python
def _build_user_message(user_text: str, resolution: TickerResolution) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, {resolution.market})\n\n"
        "Specialists available: fundamental, technical, news, macro.\n\n"
        "Call dispatch_specialists first with the specialists you want, then use the emit_* tools "
        "to stream the response. Emit order: quick_take → stock_card → sections → recommendation "
        "→ disclaimer → done."
    )
```

- [ ] **Step 4: Update `backend/app/prompts/lead_banker.md`**

Replace its contents with:

```markdown
You are the Lead Banker inside Stylobate. You drive the whole research turn:

1. Decide which specialists to consult and call `dispatch_specialists` with that list.
2. Receive the combined specialist findings as a tool result.
3. Synthesize and stream the response via the `emit_*` tools.

Specialists available: `fundamental`, `technical`, `news`, `macro`.

When to dispatch which:
- **Default deep-dive on a stock**: dispatch all four. The user usually wants the full picture.
- **"What are the fundamentals of X?"**: just `fundamental`.
- **"What are the technicals / charts / entry levels?"**: `technical` + maybe `news` (catalysts matter for timing).
- **"Anything happening with X?" / "what's the news?"**: `news` + `fundamental` for context.
- **"How does X look in this rate environment?"**: `macro` + `fundamental`.
- When in doubt, dispatch more rather than less. They run in parallel, so the wall-clock penalty is small.

After dispatch returns, emit deltas in this exact order:
1. `emit_quick_take` — one short signal line.
2. `emit_stock_card` — ticker identity + key stats. Stats should reflect what specialists returned (P/E, RSI, 10Y rate, etc.).
3. `emit_section` — include sections in this order:
   - `Thesis` — always.
   - `Fundamentals` — if `fundamental` was dispatched.
   - `Technicals` — if `technical` was dispatched.
   - `News` — if `news` was dispatched.
   - `Macro` — if `macro` was dispatched.
   - `Risks` — always.
   Markdown should reference the section's citations by index, like `[1]`.
4. `emit_recommendation` — directional call + position-size range + entry zone + stop + 12-mo target. Reconcile across specialists.
5. `emit_disclaimer` — always.
6. `emit_done` — terminate.

Discipline:
- Every numeric in `emit_section` markdown must have a matching citation in the section call.
- Recommendation signal is one of: `tactical_buy`, `accumulate`, `hold`, `reduce`.
- `position_size_range` is a 2-element percentage tuple (e.g., [2, 4]).
- Do NOT emit plain text. Tool calls only.
```

- [ ] **Step 5: Run lead_banker tests**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: 3 passed (2 existing + 1 new).

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 72 passing (71 prior + 1 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents/lead_banker.py backend/app/prompts/lead_banker.md backend/tests/test_lead_banker_agent.py
git commit -m "feat(backend): Lead Banker dispatches all 4 specialists in parallel

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Live AAPL E2E test with all 4 specialists

**Files:** none — verification only.

**Goal:** Confirm the live pipeline dispatches all four specialists in parallel and produces ≥6 sections (Thesis + Fundamentals + Technicals + News + Macro + Risks).

- [ ] **Step 1: Kill leftovers, start servers**

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null; sleep 1
```

Start backend with `run_in_background: true`:
```bash
cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1
```

Start frontend with `run_in_background: true`:
```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run dev
```

- [ ] **Step 2: Wait, healthz check**

```bash
sleep 10
curl -s http://localhost:8000/healthz
```

Expected: backend OK JSON.

- [ ] **Step 3: Get a fresh JWT**

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "$TOKEN" > /tmp/stylobate_p2b_token.txt
test -n "$TOKEN" && echo "got token" || { echo "FAILED"; exit 1; }
```

- [ ] **Step 4: Deep dive on AAPL — 4 specialists in parallel**

```bash
TOKEN=$(cat /tmp/stylobate_p2b_token.txt)
time curl -s -X POST "http://localhost:8000/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"deep dive on AAPL"}' \
  --max-time 240 > /tmp/aapl_p2b.txt
echo "=== bytes: $(wc -c < /tmp/aapl_p2b.txt) ==="
echo "=== section titles ==="
grep -E '"title":' /tmp/aapl_p2b.txt | sort -u
echo "=== section count ==="
grep -c '"type": "section"' /tmp/aapl_p2b.txt
echo "=== delta types ==="
for t in quick_take stock_card section recommendation disclaimer; do
  if grep -q "\"type\": \"$t\"" /tmp/aapl_p2b.txt; then
    echo "  ✓ $t"
  else
    echo "  ✗ $t MISSING"
  fi
done
echo "=== unique events ==="
grep "^event:" /tmp/aapl_p2b.txt | sort -u
echo "=== done count ==="
grep -c '^event: done' /tmp/aapl_p2b.txt
```

**Expected (Phase 2B success criterion):**
- All 5 delta types present
- Section count ≥ 6 (Thesis + Fundamentals + Technicals + News + Macro + Risks)
- Section titles include "News" and "Macro"
- `event: done` count = 1
- Wall-clock ≈ 90–120s (4 specialists in parallel, so roughly the slowest one's time plus orchestration overhead)

- [ ] **Step 5: Stop servers**

```bash
pkill -f "uvicorn app.main"; pkill -f "next dev"
```

---

## End-of-Phase 2B checklist

- [ ] Backend tests: 72 passing (`uv run pytest -q`)
- [ ] mypy strict + ruff clean
- [ ] Live deep-dive produces ≥6 sections including `News` and `Macro`
- [ ] News section references actual recent headlines (not LLM training data)
- [ ] Macro section references real rate numbers from FRED
- [ ] Vercel + Render auto-deploys go green
- [ ] https://stylobate.vercel.app/chat works end-to-end with 4 specialists

After Phase 2B lands, Phase 2C (India data path + crypto) is the last sub-phase to ship the spec's full Phase 2.
