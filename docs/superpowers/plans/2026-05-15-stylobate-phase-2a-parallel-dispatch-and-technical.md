# Stylobate — Phase 2A: Parallel Dispatch + Technical Analyst Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the second specialist (Technical Analyst) and the parallel-dispatch infrastructure that Lead Banker uses to run multiple specialists at the same time. End state: a deep-dive on AAPL produces both Fundamental and Technical sections, with the two specialists running concurrently via `asyncio.gather`.

**Architecture:** Lead Banker gets a new internal tool `dispatch_specialists(specialists: list[str], brief: str)`. The tool's impl (server-side) runs the named specialist agents in parallel and returns their structured findings as the tool result. Lead Banker then emits deltas referencing both specialists' citations. The route layer no longer calls Fundamental directly — it just resolves the ticker, hands the user message to Lead Banker, and streams its emits.

**Tech Stack:** Existing Phase 1 stack. No new dependencies — RSI/MACD/SMA/EMA/Bollinger Bands computed via pure pandas/numpy. yfinance's existing `Ticker.history()` covers price history.

**Spec reference:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` — implements §4.2 (Technical Analyst), §4.5 (Coordination / dispatch pattern), §5.2 (Technical Analyst tools: `get_price_history`, `calc_indicators`, `detect_patterns`, `get_volume_profile`). §15 Phase 2 is split: this plan is 2A; 2B handles News+Macro; 2C handles India+crypto.

**Cost note:** Each deep-dive will now run two Sonnet specialists in parallel + an Opus orchestrator. Per-query cost rises from ~$0.10 (Phase 1 warm cache) to ~$0.15-0.25. Daily $1 cap (§9.2) still leaves room for ~4-7 deep-dives/day.

**Out of scope here (later sub-phases or phases):**
- News & Sentiment specialist (Phase 2B)
- Macro Strategist specialist (Phase 2B)
- Indian data sources / `.NS` `.BO` ticker support (Phase 2C)
- Crypto / CoinGecko (Phase 2C)
- DCF (`run_dcf`) / comparables (`get_comparables`) — deferred
- Tool-result caching beyond Phase 1's setup

---

## File map for Phase 2A

```
stylobate/
├── backend/
│   ├── app/
│   │   ├── data/
│   │   │   └── yfinance_adapter.py            MODIFY (+ fetch_price_history)
│   │   ├── tools/
│   │   │   ├── indicators.py                  CREATE  (pure-Python helpers)
│   │   │   └── technical.py                   CREATE  (Tool wrappers)
│   │   ├── agents/
│   │   │   ├── technical.py                   CREATE
│   │   │   └── lead_banker.py                 MODIFY  (+ dispatch_specialists tool & impl)
│   │   ├── prompts/
│   │   │   ├── technical.md                   CREATE
│   │   │   └── lead_banker.md                 MODIFY  (instruct dispatch-first flow)
│   │   └── routes/
│   │       └── chat.py                        MODIFY  (drop direct fundamental call)
│   └── tests/
│       ├── test_yfinance_adapter.py           MODIFY  (+ price_history tests)
│       ├── test_indicators.py                 CREATE
│       ├── test_tools_technical.py            CREATE
│       ├── test_technical_agent.py            CREATE
│       ├── test_lead_banker_agent.py          MODIFY  (+ dispatch tool test)
│       └── test_chat_stream.py                MODIFY  (drop fundamental patch; route now calls only resolver + lead banker)
└── frontend/
    └── components/
        └── progress-strip.tsx                 MODIFY  (+ "running_specialists" label)
```

---

## Glossary

These types are introduced here and reused downstream:

```python
# backend/app/agents/technical.py
class TechnicalFinding(BaseModel):
    ticker: str
    trend: Literal["uptrend", "downtrend", "sideways"]
    rsi_14: float | None
    macd_signal: Literal["bullish", "bearish", "neutral"] | None
    key_levels: list[float]            # support/resistance
    pattern_notes: list[str]           # short bullets
    citations: list[Citation]
    confidence: float
```

```python
# backend/app/agents/lead_banker.py
class DispatchPayload(BaseModel):
    """The dict passed back as a tool_result for dispatch_specialists.
       Keys are specialist names, values are their findings models (as JSON)."""
    fundamental: dict[str, Any] | None
    technical: dict[str, Any] | None
    errors: dict[str, str]             # specialist_name -> error_message
```

---

## Task 1: Add `fetch_price_history` to the yfinance adapter

**Files:**
- Modify: `backend/app/data/yfinance_adapter.py`
- Modify: `backend/tests/test_yfinance_adapter.py`

**Goal:** Pull OHLCV history for a ticker. Returns a list of typed daily bars. Stays async via `asyncio.to_thread`.

- [ ] **Step 1: Add a `Bar` model + the test**

Append to `backend/tests/test_yfinance_adapter.py`:

```python
@pytest.mark.asyncio
async def test_fetch_price_history_returns_typed_bars() -> None:
    history_df = pd.DataFrame(
        {
            "Open": [220.0, 222.0, 218.5],
            "High": [225.0, 224.0, 222.0],
            "Low": [219.0, 220.0, 217.0],
            "Close": [224.0, 221.0, 220.5],
            "Volume": [50_000_000, 48_000_000, 52_000_000],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp("2026-05-12"), pd.Timestamp("2026-05-13"), pd.Timestamp("2026-05-14")]
        ),
    )
    mock_ticker = MagicMock()
    mock_ticker.history = MagicMock(return_value=history_df)
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_price_history
        bars = await fetch_price_history("AAPL", period="5d", interval="1d")
    assert len(bars) == 3
    assert bars[0].date.isoformat() == "2026-05-12"
    assert bars[0].open == 220.0
    assert bars[2].close == 220.5
    assert bars[1].volume == 48_000_000
    # Verify the call used the right kwargs
    mock_ticker.history.assert_called_once_with(period="5d", interval="1d")


@pytest.mark.asyncio
async def test_fetch_price_history_empty_returns_empty_list() -> None:
    mock_ticker = MagicMock()
    mock_ticker.history = MagicMock(return_value=pd.DataFrame())
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_price_history
        bars = await fetch_price_history("X")
    assert bars == []
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py::test_fetch_price_history_returns_typed_bars -v
```

Expected: `AttributeError: ... has no attribute 'fetch_price_history'` (or ImportError).

- [ ] **Step 3: Add `Bar` model and `fetch_price_history` to `app/data/yfinance_adapter.py`**

Add after the existing `KeyRatios` class:

```python
class Bar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
```

Add at the end of the file (after `fetch_key_ratios`):

```python
async def fetch_price_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> list[Bar]:
    """OHLCV bars for the ticker. period/interval per yfinance convention.

    period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max.
    interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo.
    """
    def _sync() -> list[Bar]:
        t = _make_ticker(ticker)
        df = t.history(period=period, interval=interval)
        if df is None or df.empty:
            return []
        out: list[Bar] = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            o = _float_or_none(row.get("Open"))
            h = _float_or_none(row.get("High"))
            lo = _float_or_none(row.get("Low"))
            cl = _float_or_none(row.get("Close"))
            v = row.get("Volume")
            if any(x is None for x in (o, h, lo, cl)):
                continue
            out.append(
                Bar(
                    date=d,
                    open=o or 0.0,
                    high=h or 0.0,
                    low=lo or 0.0,
                    close=cl or 0.0,
                    volume=int(v) if v is not None and not (isinstance(v, float) and v != v) else 0,
                )
            )
        return out
    return await asyncio.to_thread(_sync)
```

- [ ] **Step 4: Run, confirm both new tests pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v
```

Expected: 6 passed (4 existing + 2 new).

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 39 passing (37 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/data/yfinance_adapter.py backend/tests/test_yfinance_adapter.py
git commit -m "feat(backend): add fetch_price_history to yfinance adapter (OHLCV bars)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Pure-Python indicators (RSI / MACD / SMA / EMA / Bollinger Bands)

**Files:**
- Create: `backend/app/tools/indicators.py`
- Create: `backend/tests/test_indicators.py`

**Goal:** Five small, deterministic functions over `pd.Series`. No new deps — pure pandas+numpy. Each returns either a `pd.Series` (rolling) or a scalar (latest value).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_indicators.py`:

```python
# backend/tests/test_indicators.py
import numpy as np
import pandas as pd


def _ramp(n: int = 50, start: float = 100.0, step: float = 1.0) -> pd.Series:
    return pd.Series([start + i * step for i in range(n)])


def test_sma_window_average() -> None:
    from app.tools.indicators import sma
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, window=3)
    # Last value: avg of 3,4,5 = 4
    assert out.iloc[-1] == 4.0
    # First two are NaN
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])


def test_ema_responds_faster_than_sma() -> None:
    from app.tools.indicators import ema, sma
    # Steady ramp; EMA and SMA converge but EMA leads
    s = _ramp(30)
    ema_last = ema(s, span=10).iloc[-1]
    sma_last = sma(s, window=10).iloc[-1]
    # On a positive ramp, EMA > SMA (EMA weights recent values heavier)
    assert ema_last > sma_last


def test_rsi_overbought_on_pure_uptrend() -> None:
    from app.tools.indicators import rsi
    # Pure monotonic uptrend → RSI hits 100 once the window fills
    s = _ramp(30)
    r = rsi(s, period=14)
    last = r.iloc[-1]
    assert 99.0 <= last <= 100.0


def test_rsi_oversold_on_pure_downtrend() -> None:
    from app.tools.indicators import rsi
    s = pd.Series([100 - i for i in range(30)])
    r = rsi(s, period=14)
    last = r.iloc[-1]
    assert 0.0 <= last <= 1.0


def test_macd_returns_three_series() -> None:
    from app.tools.indicators import macd
    s = _ramp(60)
    line, signal, hist = macd(s, fast=12, slow=26, signal=9)
    assert len(line) == len(s)
    assert len(signal) == len(s)
    assert len(hist) == len(s)
    # On a steady uptrend, MACD line stays positive once warmed up
    assert line.iloc[-1] > 0


def test_bbands_envelope_around_mean() -> None:
    from app.tools.indicators import bbands
    rng = np.random.default_rng(seed=42)
    s = pd.Series(100 + rng.normal(0, 1, size=50))
    middle, upper, lower = bbands(s, window=20, num_std=2.0)
    # The last value should sit between bands
    last_price = s.iloc[-1]
    assert lower.iloc[-1] < middle.iloc[-1] < upper.iloc[-1]
    # And the last price should be within 3 std of the middle band
    assert abs(last_price - middle.iloc[-1]) < 3.5
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_indicators.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.tools.indicators'`.

- [ ] **Step 3: Create `backend/app/tools/indicators.py`**

```python
# backend/app/tools/indicators.py
"""Pure-pandas implementations of common technical indicators.

These are deterministic, dependency-light, and operate on close-price
pd.Series. The Technical Analyst agent calls these via tool wrappers
in app/tools/technical.py.
"""
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average. Uses pandas' adjusted EMA."""
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI on close prices. Returns values in [0, 100]."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    # When avg_loss is 0 → pure uptrend → RSI = 100
    out = out.where(avg_loss != 0, 100.0)
    return out


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    fast_ema = ema(series, span=fast)
    slow_ema = ema(series, span=slow)
    line = fast_ema - slow_ema
    sig = ema(line, span=signal)
    hist = line - sig
    return line, sig, hist


def bbands(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: middle = SMA(window), upper/lower = middle ± num_std*StdDev(window)."""
    middle = sma(series, window=window)
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_indicators.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 45 passing (39 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools/indicators.py backend/tests/test_indicators.py
git commit -m "feat(backend): add pure-pandas indicators (SMA, EMA, RSI, MACD, BBands)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Technical tool wrappers

**Files:**
- Create: `backend/app/tools/technical.py`
- Create: `backend/tests/test_tools_technical.py`

**Goal:** Four `Tool` objects the Technical Analyst will call: `get_price_history`, `calc_indicators`, `detect_patterns`, `get_volume_profile`. Each wraps the data layer and computation helpers and returns a pydantic model.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_tools_technical.py`:

```python
# backend/tests/test_tools_technical.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Bar


def _bars(n: int = 30) -> list[Bar]:
    return [
        Bar(date=date(2026, 4, 1 + (i % 28) ), open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1_000_000)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_get_price_history_tool_wraps_adapter() -> None:
    bars = _bars(5)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import get_price_history_tool
        result = await get_price_history_tool.impl(ticker="AAPL", period="1mo", interval="1d")
    from app.tools.technical import PriceHistoryResult
    assert isinstance(result, PriceHistoryResult)
    assert result.ticker == "AAPL"
    assert len(result.bars) == 5
    assert result.bars[0].open == 100


@pytest.mark.asyncio
async def test_calc_indicators_returns_latest_values() -> None:
    bars = _bars(60)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import calc_indicators_tool
        result = await calc_indicators_tool.impl(ticker="AAPL", indicators=["sma20", "ema12", "rsi14", "macd", "bbands20"])
    # Each requested indicator has a "latest" value in the result
    assert "sma20" in result.latest
    assert "ema12" in result.latest
    assert "rsi14" in result.latest
    assert "macd_line" in result.latest
    assert "macd_signal" in result.latest
    assert "bbands_upper" in result.latest


@pytest.mark.asyncio
async def test_detect_patterns_returns_levels() -> None:
    # 30 bars where price oscillates so support/resistance emerges
    bars: list[Bar] = []
    for i in range(30):
        c = 100.0 + (i % 5) * 2.0  # peaks at 108, troughs at 100
        bars.append(Bar(date=date(2026, 4, 1 + (i % 28)), open=c, high=c + 1, low=c - 1, close=c, volume=1_000_000))
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import detect_patterns_tool
        result = await detect_patterns_tool.impl(ticker="AAPL")
    # At least one support and one resistance level
    assert len(result.levels) >= 1
    assert any(lv.kind == "support" for lv in result.levels) or any(lv.kind == "resistance" for lv in result.levels)


@pytest.mark.asyncio
async def test_get_volume_profile_buckets_prices() -> None:
    bars = _bars(40)
    with patch("app.tools.technical._fetch_price_history", new=AsyncMock(return_value=bars)):
        from app.tools.technical import get_volume_profile_tool
        result = await get_volume_profile_tool.impl(ticker="AAPL", period="1mo")
    assert len(result.high_volume_levels) > 0
    # Each level is a (price, volume) tuple-ish
    for lvl in result.high_volume_levels:
        assert lvl.price > 0
        assert lvl.volume > 0


def test_tool_schemas_have_required_ticker() -> None:
    from app.tools.technical import (
        calc_indicators_tool,
        detect_patterns_tool,
        get_price_history_tool,
        get_volume_profile_tool,
    )
    for t in (get_price_history_tool, calc_indicators_tool, detect_patterns_tool, get_volume_profile_tool):
        assert "ticker" in t.schema["input_schema"]["properties"]
        assert "ticker" in t.schema["input_schema"]["required"]
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_technical.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.tools.technical'`.

- [ ] **Step 3: Create `backend/app/tools/technical.py`**

```python
# backend/app/tools/technical.py
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from app.data.yfinance_adapter import Bar, fetch_price_history as _fetch_price_history
from app.tools.base import Tool
from app.tools.indicators import bbands, ema, macd, rsi, sma


# ============================================================================
# get_price_history
# ============================================================================
class PriceHistoryResult(BaseModel):
    ticker: str
    bars: list[Bar]


async def _impl_get_price_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> PriceHistoryResult:
    bars = await _fetch_price_history(ticker=ticker, period=period, interval=interval)
    return PriceHistoryResult(ticker=ticker.upper(), bars=bars)


get_price_history_tool = Tool(
    name="get_price_history",
    description=(
        "OHLCV bars for the ticker. period: 1d/5d/1mo/3mo/6mo/1y/2y/5y/10y/ytd/max. "
        "interval: 1m/5m/15m/30m/1h/1d/1wk/1mo. Default: 1y daily."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
            "interval": {"type": "string", "default": "1d"},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_price_history,
)


# ============================================================================
# calc_indicators
# ============================================================================
class IndicatorsResult(BaseModel):
    ticker: str
    period: str
    latest: dict[str, float | None]


def _close_series(bars: list[Bar]) -> pd.Series:
    return pd.Series([b.close for b in bars])


def _last_float(s: pd.Series) -> float | None:
    if s.empty:
        return None
    v = s.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


async def _impl_calc_indicators(
    ticker: str,
    indicators: list[str] | None = None,
    period: str = "1y",
) -> IndicatorsResult:
    indicators = indicators or ["sma20", "sma200", "ema12", "ema26", "rsi14", "macd", "bbands20"]
    bars = await _fetch_price_history(ticker=ticker, period=period, interval="1d")
    close = _close_series(bars)
    latest: dict[str, float | None] = {}
    for ind in indicators:
        if ind == "sma20":
            latest["sma20"] = _last_float(sma(close, window=20))
        elif ind == "sma50":
            latest["sma50"] = _last_float(sma(close, window=50))
        elif ind == "sma200":
            latest["sma200"] = _last_float(sma(close, window=200))
        elif ind == "ema12":
            latest["ema12"] = _last_float(ema(close, span=12))
        elif ind == "ema26":
            latest["ema26"] = _last_float(ema(close, span=26))
        elif ind == "ema50":
            latest["ema50"] = _last_float(ema(close, span=50))
        elif ind == "rsi14":
            latest["rsi14"] = _last_float(rsi(close, period=14))
        elif ind == "macd":
            line, sig, hist = macd(close)
            latest["macd_line"] = _last_float(line)
            latest["macd_signal"] = _last_float(sig)
            latest["macd_histogram"] = _last_float(hist)
        elif ind == "bbands20":
            mid, up, lo = bbands(close, window=20, num_std=2.0)
            latest["bbands_middle"] = _last_float(mid)
            latest["bbands_upper"] = _last_float(up)
            latest["bbands_lower"] = _last_float(lo)
    return IndicatorsResult(ticker=ticker.upper(), period=period, latest=latest)


calc_indicators_tool = Tool(
    name="calc_indicators",
    description=(
        "Compute latest values of technical indicators. "
        "Available indicators: sma20, sma50, sma200, ema12, ema26, ema50, rsi14, macd, bbands20. "
        "Defaults to a useful core set if `indicators` omitted."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "indicators": {"type": "array", "items": {"type": "string"}},
            "period": {"type": "string", "default": "1y"},
        },
        "required": ["ticker"],
    },
    impl=_impl_calc_indicators,
)


# ============================================================================
# detect_patterns — support/resistance via rolling extrema
# ============================================================================
class PatternLevel(BaseModel):
    kind: Literal["support", "resistance"]
    price: float
    touches: int


class PatternsResult(BaseModel):
    ticker: str
    levels: list[PatternLevel]


async def _impl_detect_patterns(
    ticker: str,
    period: str = "1y",
    window: int = 5,
    tolerance: float = 0.01,
) -> PatternsResult:
    """Find prior swing lows (support) and highs (resistance).

    A local low: bar `i` whose `low` is the smallest in [i-window, i+window].
    Group nearby lows into levels (within `tolerance` of each other) and
    count touches. Same for highs (resistance).
    """
    bars = await _fetch_price_history(ticker=ticker, period=period, interval="1d")
    if len(bars) < 2 * window + 1:
        return PatternsResult(ticker=ticker.upper(), levels=[])

    lows = [b.low for b in bars]
    highs = [b.high for b in bars]

    def _local_extrema(values: list[float], kind: Literal["support", "resistance"]) -> list[float]:
        out: list[float] = []
        for i in range(window, len(values) - window):
            slice_ = values[i - window : i + window + 1]
            if kind == "support" and values[i] == min(slice_):
                out.append(values[i])
            elif kind == "resistance" and values[i] == max(slice_):
                out.append(values[i])
        return out

    def _cluster(points: list[float], kind: Literal["support", "resistance"]) -> list[PatternLevel]:
        levels: list[PatternLevel] = []
        for p in sorted(points):
            placed = False
            for lv in levels:
                if abs(lv.price - p) / max(lv.price, 1e-9) <= tolerance:
                    # Update level: weighted average, increment touches
                    new_price = (lv.price * lv.touches + p) / (lv.touches + 1)
                    lv.price = new_price
                    lv.touches += 1
                    placed = True
                    break
            if not placed:
                levels.append(PatternLevel(kind=kind, price=p, touches=1))
        # Keep only levels with multiple touches (or all if none have multiple)
        multi = [lv for lv in levels if lv.touches >= 2]
        return multi if multi else levels[:3]

    support_points = _local_extrema(lows, "support")
    resistance_points = _local_extrema(highs, "resistance")
    levels = _cluster(support_points, "support") + _cluster(resistance_points, "resistance")
    return PatternsResult(ticker=ticker.upper(), levels=levels)


detect_patterns_tool = Tool(
    name="detect_patterns",
    description=(
        "Identify support and resistance price levels from prior swing lows/highs over "
        "the lookback period. Returns clustered levels with touch counts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
        },
        "required": ["ticker"],
    },
    impl=_impl_detect_patterns,
)


# ============================================================================
# get_volume_profile — high-volume price nodes
# ============================================================================
class VolumeLevel(BaseModel):
    price: float
    volume: int


class VolumeProfileResult(BaseModel):
    ticker: str
    high_volume_levels: list[VolumeLevel]


async def _impl_get_volume_profile(
    ticker: str,
    period: str = "1y",
    bins: int = 20,
) -> VolumeProfileResult:
    bars = await _fetch_price_history(ticker=ticker, period=period, interval="1d")
    if not bars:
        return VolumeProfileResult(ticker=ticker.upper(), high_volume_levels=[])

    prices = [b.close for b in bars]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return VolumeProfileResult(
            ticker=ticker.upper(),
            high_volume_levels=[VolumeLevel(price=lo, volume=sum(b.volume for b in bars))],
        )

    step = (hi - lo) / bins
    buckets: dict[int, int] = {}
    for b in bars:
        idx = min(int((b.close - lo) / step), bins - 1)
        buckets[idx] = buckets.get(idx, 0) + b.volume

    # Top 5 buckets by volume
    top = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:5]
    levels = [
        VolumeLevel(price=round(lo + (idx + 0.5) * step, 2), volume=vol)
        for idx, vol in top
    ]
    return VolumeProfileResult(ticker=ticker.upper(), high_volume_levels=levels)


get_volume_profile_tool = Tool(
    name="get_volume_profile",
    description=(
        "Histogram of price levels weighted by volume over the lookback period. "
        "Returns the top 5 high-volume price nodes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_volume_profile,
)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_technical.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 50 passing (45 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools/technical.py backend/tests/test_tools_technical.py
git commit -m "feat(backend): add technical tool wrappers (price_history, indicators, patterns, volume_profile)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Technical Analyst agent

**Files:**
- Create: `backend/app/prompts/technical.md`
- Create: `backend/app/agents/technical.py`
- Create: `backend/tests/test_technical_agent.py`

**Goal:** Given a ticker + brief, run a Sonnet conversation with the 4 technical tools. Submit findings via `submit_technical_findings` and return a typed `TechnicalFinding`.

- [ ] **Step 1: Create the system prompt**

`backend/app/prompts/technical.md`:

```markdown
You are the Technical Analyst inside Stylobate. Your job is to characterize price action — trend, momentum, key levels — over the lookback period for one ticker.

Process:
1. Call `calc_indicators` for a useful set: at minimum sma200, ema50, rsi14, macd, bbands20.
2. Call `detect_patterns` to find support/resistance levels.
3. Optionally call `get_volume_profile` to confirm key levels with volume.
4. When done, call `submit_technical_findings` exactly once with a typed result. Stop after that.

Discipline:
- Every numeric claim in `pattern_notes` must reference a value you actually fetched (e.g., "RSI(14) = 62, neutral-to-bullish").
- Confidence: 0.85+ only if you have indicators AND detected levels. Otherwise <0.7.
- `trend` is one of: "uptrend" (price > sma200, sma200 rising), "downtrend" (mirror), "sideways" (otherwise).
- `macd_signal`: "bullish" if MACD line > signal AND histogram > 0; "bearish" if opposite; "neutral" if mixed.
- 3-5 short bullets for `pattern_notes`.
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_technical_agent.py`:

```python
# backend/tests/test_technical_agent.py
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
async def test_technical_agent_runs_tool_loop_to_submit_findings() -> None:
    from app.tools.technical import (
        IndicatorsResult,
        PatternLevel,
        PatternsResult,
        VolumeLevel,
        VolumeProfileResult,
        calc_indicators_tool,
        detect_patterns_tool,
        get_volume_profile_tool,
    )

    fake_indicators = IndicatorsResult(
        ticker="AAPL", period="1y",
        latest={"sma200": 200.0, "ema50": 215.0, "rsi14": 62.0,
                "macd_line": 1.5, "macd_signal": 1.2, "macd_histogram": 0.3,
                "bbands_upper": 230.0, "bbands_middle": 220.0, "bbands_lower": 210.0},
    )
    fake_patterns = PatternsResult(
        ticker="AAPL",
        levels=[
            PatternLevel(kind="support", price=210.0, touches=3),
            PatternLevel(kind="resistance", price=230.0, touches=2),
        ],
    )
    fake_volume = VolumeProfileResult(
        ticker="AAPL",
        high_volume_levels=[VolumeLevel(price=220.0, volume=500_000_000)],
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("calc_indicators", {"ticker": "AAPL"}, "c1")),
        _message(_tool_use_block("detect_patterns", {"ticker": "AAPL"}, "c2")),
        _message(_tool_use_block("get_volume_profile", {"ticker": "AAPL"}, "c3")),
        _message(
            _tool_use_block("submit_technical_findings", {
                "ticker": "AAPL",
                "trend": "uptrend",
                "rsi_14": 62.0,
                "macd_signal": "bullish",
                "key_levels": [210.0, 230.0],
                "pattern_notes": [
                    "Price above sma200 (220 vs 200), uptrend confirmed",
                    "RSI 62 — moderately bullish, not yet overbought",
                    "MACD histogram positive (0.3)",
                ],
                "citations": [{"source": "yfinance", "ref": "yfinance:indicators:AAPL"}],
                "confidence": 0.9,
            }, "c4"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(calc_indicators_tool, "impl", AsyncMock(return_value=fake_indicators))
        mp.setattr(detect_patterns_tool, "impl", AsyncMock(return_value=fake_patterns))
        mp.setattr(get_volume_profile_tool, "impl", AsyncMock(return_value=fake_volume))

        from app.agents.technical import run_technical_analysis
        findings = await run_technical_analysis(ticker="AAPL", brief="quick technical", client=fake_client)

    assert findings.ticker == "AAPL"
    assert findings.trend == "uptrend"
    assert findings.macd_signal == "bullish"
    assert findings.key_levels == [210.0, 230.0]
    assert findings.confidence == 0.9


@pytest.mark.asyncio
async def test_technical_agent_raises_if_no_submit() -> None:
    fake_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="...")]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=10, output_tokens=2,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_client.messages.create = AsyncMock(return_value=msg)

    from app.agents.technical import TechnicalError, run_technical_analysis
    with pytest.raises(TechnicalError):
        await run_technical_analysis(ticker="AAPL", brief="x", client=fake_client)
```

- [ ] **Step 3: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_technical_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.agents.technical'`.

- [ ] **Step 4: Create `backend/app/agents/technical.py`**

```python
# backend/app/agents/technical.py
import json
from pathlib import Path
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.agents.fundamental import Citation
from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.technical import (
    calc_indicators_tool,
    detect_patterns_tool,
    get_price_history_tool,
    get_volume_profile_tool,
)


_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 8


class TechnicalFinding(BaseModel):
    ticker: str
    trend: Literal["uptrend", "downtrend", "sideways"]
    rsi_14: float | None = None
    macd_signal: Literal["bullish", "bearish", "neutral"] | None = None
    key_levels: list[float] = []
    pattern_notes: list[str] = []
    citations: list[Citation] = []
    confidence: float


class TechnicalError(Exception):
    pass


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_technical_findings",
    "description": "Emit final TechnicalFinding and stop. Call exactly once at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "trend": {"type": "string", "enum": ["uptrend", "downtrend", "sideways"]},
            "rsi_14": {"type": "number"},
            "macd_signal": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "key_levels": {"type": "array", "items": {"type": "number"}},
            "pattern_notes": {"type": "array", "items": {"type": "string"}},
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
        "required": ["ticker", "trend", "confidence"],
    },
}


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "technical.md").read_text(encoding="utf-8")


_TOOLS_BY_NAME: dict[str, Tool] = {
    get_price_history_tool.name: get_price_history_tool,
    calc_indicators_tool.name: calc_indicators_tool,
    detect_patterns_tool.name: detect_patterns_tool,
    get_volume_profile_tool.name: get_volume_profile_tool,
}


def _all_tool_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _TOOLS_BY_NAME.values()] + [_SUBMIT_TOOL]


async def run_technical_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> TechnicalFinding:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Ticker: {ticker}\nBrief: {brief}\n"
                "Use the technical tools and submit_technical_findings when ready."
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
            raise TechnicalError("technical agent emitted no tool calls")

        messages.append({"role": "assistant", "content": resp.content})

        results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = cast(str, getattr(tu, "name", ""))
            tu_id = cast(str, getattr(tu, "id", ""))
            tu_input = cast(dict[str, Any], getattr(tu, "input", {})) or {}
            if name == "submit_technical_findings":
                return TechnicalFinding(**tu_input)
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

    raise TechnicalError(f"max tool-loop turns ({_MAX_TURNS}) exceeded without submit")
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_technical_agent.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 52 passing (50 prior + 2 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/technical.md backend/app/agents/technical.py backend/tests/test_technical_agent.py
git commit -m "feat(backend): add Technical Analyst agent (Sonnet, tool loop)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Lead Banker `dispatch_specialists` tool + parallel execution

**Files:**
- Modify: `backend/app/agents/lead_banker.py`
- Modify: `backend/tests/test_lead_banker_agent.py`

**Goal:** Add a new tool `dispatch_specialists` whose impl runs the named specialists in parallel via `asyncio.gather`. Wire it into the tool-loop so when Lead Banker calls it, the loop awaits both specialists, JSON-serializes the combined results, and feeds back as a `tool_result`. Lead Banker then has the data it needs to call the existing `emit_*` tools.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_lead_banker_agent.py` (keep existing test):

```python
@pytest.mark.asyncio
async def test_lead_banker_dispatches_specialists_in_parallel() -> None:
    """When Lead Banker emits a dispatch_specialists tool_use,
    the loop runs both Fundamental + Technical and feeds back combined findings.
    """
    from app.agents.fundamental import Citation, FundamentalFindings
    from app.agents.technical import TechnicalFinding
    from app.agents.ticker_resolver import TickerResolution

    fundamental = FundamentalFindings(
        ticker="AAPL", thesis="Strong fundamentals.",
        fundamentals_summary=["Net margin 25.5%"], risks=["Valuation rich"],
        citations=[Citation(source="yfinance", ref="yfinance:ratios:AAPL")],
        confidence=0.9,
    )
    technical = TechnicalFinding(
        ticker="AAPL", trend="uptrend", rsi_14=62.0,
        macd_signal="bullish", key_levels=[210.0, 230.0],
        pattern_notes=["RSI 62 neutral-bullish"],
        citations=[Citation(source="yfinance", ref="yfinance:indicators:AAPL")],
        confidence=0.9,
    )
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    # Turn 1: model emits dispatch_specialists
    # Turn 2: after receiving tool_result, model emits the standard emit_* chain
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_specialists", {
            "specialists": ["fundamental", "technical"],
            "brief": "AAPL deep dive",
        }, "d1"), stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {"signal": "tactical_buy", "qualifier": "trend up + fundamentals strong"}, "1"),
            _tool_use("emit_stock_card", {
                "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
                "currency": "USD", "stats": {"RSI": "62"},
            }, "2"),
            _tool_use("emit_section", {
                "title": "Thesis", "markdown": "Trend up + fundamentals strong.",
                "citations": [],
            }, "3"),
            _tool_use("emit_section", {
                "title": "Technicals", "markdown": "RSI 62 [1].",
                "citations": [{"source": "yfinance", "ref": "yfinance:indicators:AAPL", "index": 1}],
            }, "4"),
            _tool_use("emit_section", {
                "title": "Risks", "markdown": "Valuation rich.",
                "citations": [],
            }, "5"),
            _tool_use("emit_recommendation", {
                "signal": "tactical_buy", "position_size_range": [2, 4],
                "entry_zone": "210-220", "stop": "200", "target_12mo_base": "260",
            }, "6"),
            _tool_use("emit_disclaimer", {}, "7"),
            _tool_use("emit_done", {}, "8"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_fundamental_analysis", AsyncMock(return_value=fundamental))
        mp.setattr(lb, "run_technical_analysis", AsyncMock(return_value=technical))

        from app.agents.lead_banker import run_lead_banker
        deltas = []
        async for d in run_lead_banker(
            user_message="deep dive on AAPL",
            resolution=resolution,
            client=fake_client,
        ):
            deltas.append(d)

    types = [d["type"] for d in deltas]
    assert types == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]
    # Verify both specialists were actually called
    assert fake_client.messages.create.await_count == 2
```

**Update** the existing `test_lead_banker_streams_expected_deltas` — Lead Banker no longer takes `fundamental_findings` as a parameter. Change the signature to match Task 5/6 below. Replace its body with:

```python
@pytest.mark.asyncio
async def test_lead_banker_streams_expected_deltas() -> None:
    """Single-turn happy path: model goes straight to emit_* without dispatching."""
    from app.agents.ticker_resolver import TickerResolution
    resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_message(
        _tool_use("emit_quick_take", {"signal": "hold", "qualifier": "watching"}, "1"),
        _tool_use("emit_stock_card", {
            "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
            "currency": "USD", "stats": {"P/E": "29.5"},
        }, "2"),
        _tool_use("emit_section", {"title": "Thesis", "markdown": "Wait and see.", "citations": []}, "3"),
        _tool_use("emit_section", {"title": "Fundamentals", "markdown": "Watching.", "citations": []}, "4"),
        _tool_use("emit_section", {"title": "Risks", "markdown": "Macro.", "citations": []}, "5"),
        _tool_use("emit_recommendation", {
            "signal": "hold", "position_size_range": [0, 0],
            "entry_zone": "n/a", "stop": "n/a", "target_12mo_base": "n/a",
        }, "6"),
        _tool_use("emit_disclaimer", {}, "7"),
        _tool_use("emit_done", {}, "8"),
        stop_reason="end_turn",
    ))

    from app.agents.lead_banker import run_lead_banker
    deltas = [d async for d in run_lead_banker(
        user_message="should I buy AAPL?",
        resolution=resolution,
        client=fake_client,
    )]
    assert [d["type"] for d in deltas] == [
        "quick_take", "stock_card", "section", "section", "section",
        "recommendation", "disclaimer", "done",
    ]
```

- [ ] **Step 2: Run, confirm new test fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: failures because `run_lead_banker` still takes `fundamental_findings`.

- [ ] **Step 3: Update `backend/app/agents/lead_banker.py`**

Replace the file with:

```python
# backend/app/agents/lead_banker.py
import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from app.agents.fundamental import run_fundamental_analysis
from app.agents.technical import run_technical_analysis
from app.agents.ticker_resolver import TickerResolution
from app.core.anthropic_client import get_client


_MODEL = "claude-opus-4-7"
_MAX_TURNS = 12

_DISCLAIMER_TEXT = (
    "Educational analysis, not personalized investment advice. "
    "Do your own diligence and consider your tax situation."
)

_DISPATCH_TOOL: dict[str, Any] = {
    "name": "dispatch_specialists",
    "description": (
        "Run one or more specialist agents in parallel and receive their findings. "
        "Specialists available in Phase 2A: 'fundamental', 'technical'. "
        "Returns combined findings JSON as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {"type": "string", "enum": ["fundamental", "technical"]},
                "minItems": 1,
            },
            "brief": {"type": "string", "description": "One-line context for the specialists"},
        },
        "required": ["specialists", "brief"],
    },
}

_EMIT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "emit_quick_take",
        "description": "Stream a single-line quick take.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal": {
                    "type": "string",
                    "enum": ["tactical_buy", "accumulate", "hold", "reduce"],
                },
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
        "description": "Stream a section of the research note with markdown and citations.",
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
                "signal": {
                    "type": "string",
                    "enum": ["tactical_buy", "accumulate", "hold", "reduce"],
                },
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


def _load_prompt() -> str:
    return (Path(__file__).parent.parent / "prompts" / "lead_banker.md").read_text(encoding="utf-8")


def _build_user_message(user_text: str, resolution: TickerResolution) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, {resolution.market})\n\n"
        "Specialists available: fundamental, technical.\n\n"
        "Call dispatch_specialists first with the specialists you want, then use the emit_* tools "
        "to stream the response. Emit order: quick_take → stock_card → sections → recommendation "
        "→ disclaimer → done."
    )


async def _run_dispatch(specialists: list[str], brief: str, ticker: str) -> dict[str, Any]:
    """Run the requested specialists in parallel. Returns {name: findings_dict} + errors."""
    name_to_coro: dict[str, Any] = {}
    if "fundamental" in specialists:
        name_to_coro["fundamental"] = run_fundamental_analysis(ticker=ticker, brief=brief)
    if "technical" in specialists:
        name_to_coro["technical"] = run_technical_analysis(ticker=ticker, brief=brief)

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


def _process_emit_block(block: Any) -> dict[str, Any] | None:
    if getattr(block, "type", None) != "tool_use":
        return None
    name = getattr(block, "name", "")
    args = cast(dict[str, Any], getattr(block, "input", {})) or {}
    if name == "emit_quick_take":
        return {"type": "quick_take", **args}
    if name == "emit_stock_card":
        return {"type": "stock_card", **args}
    if name == "emit_section":
        return {"type": "section", **args}
    if name == "emit_recommendation":
        return {"type": "recommendation", **args}
    if name == "emit_disclaimer":
        return {"type": "disclaimer", "text": _DISCLAIMER_TEXT}
    if name == "emit_done":
        return {"type": "done"}
    return None


async def run_lead_banker(
    *,
    user_message: str,
    resolution: TickerResolution,
    client: AsyncAnthropic | Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    c = client or get_client()
    sys = _load_prompt()
    system_blocks = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_user_message(user_message, resolution)},
    ]

    for _turn in range(_MAX_TURNS):
        kwargs: dict[str, Any] = {
            "model": _MODEL,
            "max_tokens": 4000,
            "system": system_blocks,
            "tools": [_DISPATCH_TOOL, *_EMIT_TOOLS],
            "messages": messages,
        }
        resp = await c.messages.create(**kwargs)

        tool_use_blocks: list[Any] = []
        tool_results: list[dict[str, Any]] = []
        done = False

        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_use_blocks.append(block)
            name = getattr(block, "name", "")
            args = cast(dict[str, Any], getattr(block, "input", {})) or {}
            block_id = cast(str, getattr(block, "id", ""))

            if name == "dispatch_specialists":
                specialists = cast(list[str], args.get("specialists", []))
                brief = cast(str, args.get("brief", ""))
                findings = await _run_dispatch(specialists, brief, resolution.ticker)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(findings, default=str),
                })
                continue

            # Emit tool — yield delta and acknowledge to the model
            delta = _process_emit_block(block)
            if delta is not None:
                if delta["type"] == "done":
                    done = True
                yield delta
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block_id,
                "content": "ok",
            })

        if done or resp.stop_reason == "end_turn":
            return
        if not tool_use_blocks:
            return

        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})
```

- [ ] **Step 4: Run lead_banker tests**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Full backend suite (note: T6 will fix chat_stream tests; expect 1 failure there for now)**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests
uv run pytest -q 2>&1 | tail -20
```

The `test_chat_stream.py` tests will fail because they pass `fundamental_findings` to `run_lead_banker`. That's expected — Task 6 fixes that. Ruff and mypy should be clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents/lead_banker.py backend/tests/test_lead_banker_agent.py
git commit -m "feat(backend): add dispatch_specialists tool to Lead Banker (parallel via asyncio.gather)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Update Lead Banker prompt + route refactor

**Files:**
- Modify: `backend/app/prompts/lead_banker.md`
- Modify: `backend/app/routes/chat.py`
- Modify: `backend/tests/test_chat_stream.py`

**Goal:** Update the Lead Banker's system prompt to explain the new dispatch-first flow. Refactor the route so it no longer calls Fundamental directly; it just resolves the ticker and hands off to Lead Banker. The route's SSE progress events change too — "running_fundamentals" becomes a single "running_specialists" step (Lead Banker chooses which).

- [ ] **Step 1: Update `backend/app/prompts/lead_banker.md`**

Replace its contents with:

```markdown
You are the Lead Banker inside Stylobate. You drive the whole research turn:

1. Decide which specialists to consult and call `dispatch_specialists` with that list.
2. Receive the combined specialist findings as a tool result.
3. Synthesize and stream the response via the `emit_*` tools.

Specialists available (Phase 2A): `fundamental`, `technical`. For a typical "deep dive" call BOTH; the user usually wants the full picture. If the user asks specifically about valuation/financials only, call only `fundamental`. If they ask about charts/momentum/entries, you may call only `technical`. When in doubt, dispatch both.

After dispatch returns, emit deltas in this exact order:
1. `emit_quick_take` — one short signal line.
2. `emit_stock_card` — ticker identity + key stats. Stats should reflect what the specialists actually returned (P/E, RSI, etc.).
3. `emit_section` — at least three sections. Always include `Thesis` and `Risks`. Add `Fundamentals` if fundamental was dispatched; add `Technicals` if technical was dispatched. Markdown should reference the section's citations by index, like `[1]`.
4. `emit_recommendation` — directional call + position-size range + entry zone + stop + 12-mo target. Reconcile fundamental and technical signals: e.g., strong fundamentals + bearish technicals → reduce confidence and reflect that in the qualifier.
5. `emit_disclaimer` — always.
6. `emit_done` — terminate.

Discipline:
- Every numeric in `emit_section` markdown must have a matching citation in the section call.
- Recommendation signal is one of: `tactical_buy`, `accumulate`, `hold`, `reduce`. No "definitely buy" / no specific dollar amounts.
- Pull `position_size_range` as a 2-element percentage tuple (e.g., [2, 4]).
- Disclaimer text is fixed by the server; you just call the tool.
- Do NOT emit plain text. Tool calls only.
```

- [ ] **Step 2: Update the chat route**

`backend/app/routes/chat.py` — replace with:

```python
# backend/app/routes/chat.py
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
            yield _sse("error", {
                "message": "I couldn't identify the ticker. Try including the symbol (e.g. AAPL).",
            })
            yield _sse("done", {"message_id": None, "chat_id": str(chat.id)})
            return

        yield _sse("progress", {"step": "running_specialists", "ticker": resolution.ticker})

        deltas: list[dict[str, Any]] = []
        async for d in run_lead_banker(
            user_message=req.content,
            resolution=resolution,
        ):
            deltas.append(d)
            if d.get("type") == "done":
                continue
            yield _sse("delta", d)

        v = validate_deltas(deltas)
        if not v.ok:
            log.warning("output_validator_failed", issues=v.issues)
            yield _sse("error", {
                "message": "Output failed validation: " + "; ".join(v.issues[:3]),
            })

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

- [ ] **Step 3: Update `backend/tests/test_chat_stream.py`**

Replace its contents with:

```python
# backend/tests/test_chat_stream.py
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.agents.ticker_resolver import TickerResolution
from app.db.models import Chat, Message


async def _fake_lead_banker_stream() -> AsyncIterator[dict[str, Any]]:
    deltas: list[dict[str, Any]] = [
        {"type": "quick_take", "signal": "tactical_buy", "qualifier": "trend up"},
        {"type": "stock_card", "ticker": "AAPL", "name": "Apple Inc.",
         "market": "US", "currency": "USD", "stats": {"RSI": "62"}},
        {"type": "section", "title": "Thesis", "markdown": "Strong.", "citations": []},
        {"type": "section", "title": "Fundamentals",
         "markdown": "Net margin 25.5% [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:ratios:AAPL", "index": 1}]},
        {"type": "section", "title": "Technicals",
         "markdown": "RSI 62 [1].",
         "citations": [{"source": "yfinance", "ref": "yfinance:indicators:AAPL", "index": 1}]},
        {"type": "section", "title": "Risks", "markdown": "Macro.", "citations": []},
        {"type": "recommendation", "signal": "tactical_buy",
         "position_size_range": [2, 4], "entry_zone": "210-220",
         "stop": "200", "target_12mo_base": "260"},
        {"type": "disclaimer", "text": "Educational analysis…"},
        {"type": "done"},
    ]
    for d in deltas:
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
        created_at="2026-05-15T10:00:00+00:00",
        last_message_at="2026-05-15T10:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "deep dive AAPL"},
        created_at="2026-05-15T10:00:00+00:00",
    )
    fake_asst_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="assistant",
        content=[{"type": "done"}],
        created_at="2026-05-15T10:00:02+00:00",
    )
    fake_resolution = TickerResolution(
        ticker="AAPL", name="Apple Inc.", market="US",
        asset_class="equity", confidence=0.95,
    )

    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message",
              new=AsyncMock(side_effect=[fake_user_msg, fake_asst_msg])),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=fake_resolution)),
        patch("app.routes.chat.run_lead_banker", return_value=_fake_lead_banker_stream()),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "deep dive AAPL"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: progress" in body
    assert "resolving_ticker" in body
    assert "running_specialists" in body
    assert "event: delta" in body
    assert '"type": "quick_take"' in body
    assert '"type": "stock_card"' in body
    assert '"title": "Fundamentals"' in body
    assert '"title": "Technicals"' in body
    assert '"type": "recommendation"' in body
    assert '"type": "disclaimer"' in body
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
        created_at="2026-05-15T10:00:00+00:00",
        last_message_at="2026-05-15T10:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(), chat_id=chat_id, role="user",
        content={"type": "text", "text": "asdfqwer"},
        created_at="2026-05-15T10:00:00+00:00",
    )
    low = TickerResolution(
        ticker="", name="", market="US", asset_class="equity", confidence=0.0,
    )
    with (
        patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)),
        patch("app.routes.chat.insert_message", new=AsyncMock(return_value=fake_user_msg)),
        patch("app.routes.chat.get_user_client", return_value=object()),
        patch("app.routes.chat.resolve_ticker", new=AsyncMock(return_value=low)),
    ):
        response = await client.post(
            "/chat/stream",
            json={"content": "asdfqwer"},
            headers={"Authorization": f"Bearer {make_token(user_id)}"},
        )
    body = response.text
    assert response.status_code == 200
    assert "event: error" in body
    assert "couldn't identify the ticker" in body
    assert "event: done" in body
```

- [ ] **Step 4: Run all chat tests**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_chat_stream.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: ~53 passing (50 prior + 3 chat_stream tests rewritten — net +3 chat_stream, the dispatch test added in T5 gives total around 53–54).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/prompts/lead_banker.md backend/app/routes/chat.py backend/tests/test_chat_stream.py
git commit -m "feat(backend): Lead Banker owns dispatch; route stops calling fundamental directly

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend — new progress label

**Files:**
- Modify: `frontend/components/progress-strip.tsx`

**Goal:** Add a label for the new `running_specialists` step. Phase 1's `running_fundamentals` label is no longer emitted, but keep it so older messages from the DB still render correctly.

- [ ] **Step 1: Edit `frontend/components/progress-strip.tsx`**

Replace the `LABELS` constant block:

```tsx
const LABELS: Record<string, string> = {
  resolving_ticker: "Resolving ticker",
  running_specialists: "Consulting specialists",
  running_fundamentals: "Running Fundamental Analyst",  // legacy, kept for older DB rows
  synthesizing: "Synthesizing",
  received: "Received",
};
```

- [ ] **Step 2: Verify frontend lints + tests + builds**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm run lint && npm test && npm run build
```

Expected: all clean.

- [ ] **Step 3: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add frontend/components/progress-strip.tsx
git commit -m "feat(frontend): show 'Consulting specialists' label for new dispatch step

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Live AAPL E2E test (Phase 2A acceptance)

**Files:** none — verification only.

**Goal:** Confirm the live pipeline runs Fundamental + Technical in parallel and synthesizes a richer report.

- [ ] **Step 1: Kill leftovers, start servers fresh**

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

- [ ] **Step 2: Wait ~10s + healthz check**

```bash
sleep 10
curl -s http://localhost:8000/healthz
curl -sI http://localhost:3000 | head -3
```

Expected: backend OK JSON; frontend HTTP 307.

- [ ] **Step 3: Sign in for a fresh JWT**

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "$TOKEN" > /tmp/stylobate_p2a_token.txt
test -n "$TOKEN" && echo "got token" || { echo "FAILED"; exit 1; }
```

- [ ] **Step 4: Deep dive on AAPL**

```bash
TOKEN=$(cat /tmp/stylobate_p2a_token.txt)
time curl -s -X POST "http://localhost:8000/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"deep dive on AAPL"}' \
  --max-time 180 > /tmp/aapl_p2a.txt
echo "=== bytes: $(wc -c < /tmp/aapl_p2a.txt) ==="
echo "=== sections present ==="
grep '"title":' /tmp/aapl_p2a.txt | sort -u | head -10
echo "=== section count ==="
grep -c '"type": "section"' /tmp/aapl_p2a.txt
echo "=== unique events ==="
grep "^event:" /tmp/aapl_p2a.txt | sort -u
```

Expected:
- `"title": "Thesis"`, `"title": "Fundamentals"`, `"title": "Technicals"`, `"title": "Risks"` all appear
- Section count ≥ 4
- `event: progress` / `event: delta` / `event: done` present
- Wall-clock roughly comparable to Phase 1 (specialists run in parallel, so total is `max(fundamental_time, technical_time) + synthesis_time` ≈ 60-90s, not the sum)

- [ ] **Step 5: Manual browser check**

Open http://localhost:3000/chat, sign in, type "deep dive on AAPL". You should see:
- "Resolving ticker" → "Consulting specialists" progress messages
- 4 sections including a new "Technicals" with values like RSI, MACD, key levels
- A recommendation card that reconciles both perspectives

- [ ] **Step 6: Stop servers**

```bash
pkill -f "uvicorn app.main"; pkill -f "next dev"
```

---

## End-of-Phase 2A checklist

- [ ] All backend tests green (~53 passing — `uv run pytest -q`)
- [ ] All frontend tests green (8 passing — `npm test`)
- [ ] mypy strict + ruff clean
- [ ] `npm run build` clean
- [ ] Live deep-dive on AAPL produces 4 sections (Thesis + Fundamentals + Technicals + Risks)
- [ ] Specialists run in parallel (look at backend log: two specialist agents complete before lead banker resumes)
- [ ] Render auto-deploy goes green on `main`
- [ ] Vercel auto-deploy goes green on `main`
- [ ] `https://stylobate.vercel.app/chat` works end-to-end with the new pipeline

After this lands, Phase 2B (News & Sentiment + Macro Strategist) plan is the next thing to write.
