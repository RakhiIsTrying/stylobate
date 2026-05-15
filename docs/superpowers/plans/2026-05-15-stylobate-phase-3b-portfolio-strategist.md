# Stylobate — Phase 3B: Portfolio Strategist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Portfolio Strategist Sonnet agent end-to-end — pure stats math, benchmark/FX adapters, 4 tools, the agent itself, Lead Banker integration with `portfolio_mode`, two routing paths (`/chat/stream` keyword detection + dedicated `/chat/portfolio`), and an "Analyze portfolio" button.

**Architecture:** Phase 3B extends the Phase 2 specialist pattern (Sonnet agent + Tool wrappers + parallel dispatch). The strategist reads holdings from Supabase via `app/db/portfolios.py` (Phase 3A) with **closure-bound `user_id`** so prompt injection can't spoof identity. Per-cohort stats use yfinance benchmarks (`^GSPC`/`^NSEI`/`BTC-USD`) cached 1h in `cache_kv`. FX (USDINR only in 3B) supports cross-cohort rebalance comparison without abandoning native-currency reporting.

**Tech Stack:** Existing — FastAPI, Pydantic v2, asyncpg, Anthropic Sonnet 4.6, yfinance, cache_kv. No new external deps.

**Spec reference:** `docs/superpowers/specs/2026-05-15-stylobate-phase-3b-portfolio-strategist-design.md`. Every spec section maps to one or more tasks below.

---

## File map

```
backend/
  app/
    data/
      portfolio_stats.py            NEW — pure math (T1)
      benchmarks.py                 NEW — yfinance + cache_kv 1h (T2)
      fx.py                         NEW — USDINR + cache_kv 1h (T2)
    models/
      portfolio_strategist.py       NEW — Pydantic models (T3)
    tools/
      portfolio.py                  NEW — 4 closure-bound Tool wrappers (T3)
    agents/
      portfolio_strategist.py       NEW — Sonnet agent (T4)
      lead_banker.py                MODIFY — dispatch_portfolio_strategist, portfolio_mode (T6)
    routes/
      chat_portfolio.py             NEW — POST /chat/portfolio (T5)
      chat.py                       MODIFY — keyword detection (T7)
    prompts/
      portfolio_strategist.md       NEW — agent system prompt (T4)
      lead_banker.md                MODIFY — portfolio_mode section (T6)
    main.py                         MODIFY — register chat_portfolio router (T5)
  tests/
    test_data_portfolio_stats.py            NEW (T1)
    test_data_benchmarks.py                 NEW (T2)
    test_data_fx.py                         NEW (T2)
    test_tools_portfolio.py                 NEW (T3)
    test_portfolio_strategist_agent.py      NEW (T4)
    test_routes_chat_portfolio_endpoint.py  NEW (T5)
    test_lead_banker_portfolio_mode.py      NEW (T6)
    test_routes_chat_portfolio_mode.py      NEW (T7)
    test_routes_e2e_portfolio_analysis.py   NEW (T10)

frontend/
  components/portfolio-tab.tsx              MODIFY — Analyze button (T8)
  app/chat/page.tsx                         MODIFY — prefill from searchParams (T8)
  lib/api-resolve.ts                        UNCHANGED
  e2e/portfolio.spec.ts                     MODIFY — + analyze test (T9)
```

---

## Glossary

- **Cohort** = `(currency, asset_class_group)` where `asset_class_group` is `equity_etf` (for asset_class ∈ {equity, etf}) or `crypto` (for asset_class = crypto). USD-equity and USD-crypto are SEPARATE cohorts even though both quote in USD.
- **Closure-bound user_id:** The 4 portfolio tools capture `user_id` (and optional `portfolio_id`) in their `impl` closure at agent-construction time. The LLM can pass arguments to the tool, but `user_id` is never one of them.
- **portfolio_mode:** A bool flag passed to `run_lead_banker` from `/chat/stream`'s portfolio path. Skips ticker resolution; surfaces `(portfolio_mode=True)` to the LLM in the user message; Lead Banker dispatches only the portfolio strategist (not Phase 2 specialists).
- **Native currency:** Each position's `currency` field (USD, INR). Per-cohort stats stay in their native currency — no FX conversion in aggregates. FX is used only inside `suggest_rebalance`'s cross-cohort comparison.

---

## Pre-flight

- [ ] **Step P1: Confirm baseline tests pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -q
```

Expected: 123 passing (Phase 3A end state).

- [ ] **Step P2: Confirm lint + types clean**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests
```

Expected: `All checks passed!` + `Success: no issues found in 77 source files`.

- [ ] **Step P3: Verify Phase 3A artifacts exist**

```bash
ls /Users/rakhisinha/Stylobate/backend/app/db/portfolios.py \
   /Users/rakhisinha/Stylobate/backend/app/data/prices_cache.py \
   /Users/rakhisinha/Stylobate/backend/app/db/prices.py 2>&1
```

Expected: all three files print without error.

---

## Task 1: Pure math — `portfolio_stats.py`

**Files:**
- Create: `backend/app/data/portfolio_stats.py`
- Create: `backend/tests/test_data_portfolio_stats.py`

**Goal:** A no-I/O module that takes positions + price-history + benchmark-history + risk-free rate, returns a list of `CohortStats` dicts. Tested with synthetic fixtures — no yfinance, no DB.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_data_portfolio_stats.py`:

```python
from __future__ import annotations

from datetime import date, timedelta


def _days(n: int, start: float, step: float) -> list[tuple[date, float]]:
    """Helper: n daily closes starting at `start`, increasing by `step`."""
    d0 = date(2025, 5, 15)
    return [(d0 + timedelta(days=i), start + step * i) for i in range(n)]


def test_cohort_key_buckets_usd_equity_and_crypto_separately() -> None:
    from app.data.portfolio_stats import cohort_key
    assert cohort_key({"currency": "USD", "asset_class": "equity"}) == ("USD", "equity_etf")
    assert cohort_key({"currency": "USD", "asset_class": "etf"}) == ("USD", "equity_etf")
    assert cohort_key({"currency": "USD", "asset_class": "crypto"}) == ("USD", "crypto")
    assert cohort_key({"currency": "INR", "asset_class": "equity"}) == ("INR", "equity_etf")


def test_weights_sum_to_one_within_cohort() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "cost_basis": 175.0, "current_price": 200.0},
        {"ticker": "MSFT", "currency": "USD", "asset_class": "equity",
         "quantity": 5, "cost_basis": 400.0, "current_price": 450.0},
    ]
    out = compute_cohort_stats(
        positions, price_history={}, benchmark_history={}, risk_free_rate={},
    )
    assert len(out) == 1
    c = out[0]
    assert c["cohort"] == ("USD", "equity_etf")
    # AAPL value = 2000, MSFT = 2250, total = 4250
    assert abs(c["weights"]["AAPL"] - 2000 / 4250) < 1e-6
    assert abs(c["weights"]["MSFT"] - 2250 / 4250) < 1e-6
    assert abs(sum(c["weights"].values()) - 1.0) < 1e-9


def test_gain_pct_uses_cost_basis() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "cost_basis": 100.0, "current_price": 110.0},
    ]
    out = compute_cohort_stats(positions, {}, {}, {})
    # cost 1000, value 1100 → +10%
    assert abs(out[0]["gain_pct"] - 10.0) < 1e-9


def test_returns_1y_computed_from_price_history() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "cost_basis": 100.0, "current_price": 200.0},
    ]
    # 252 trading days; price 100 → 200 = +100%
    history = {"AAPL": _days(252, start=100.0, step=100 / 251)}
    out = compute_cohort_stats(positions, {"AAPL": history["AAPL"]}, {}, {})
    assert out[0]["returns_1y"] is not None
    assert abs(out[0]["returns_1y"] - 100.0) < 0.5


def test_sharpe_positive_when_trend_up_with_low_vol() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 1, "cost_basis": 100.0, "current_price": 110.0},
    ]
    # steady upward, low noise → sharpe > 1
    history = {"AAPL": _days(252, start=100.0, step=0.05)}
    out = compute_cohort_stats(
        positions,
        {"AAPL": history["AAPL"]},
        {("USD", "equity_etf"): _days(252, start=4000.0, step=2.0)},
        {("USD", "equity_etf"): 4.5},  # 4.5% rf
    )
    s = out[0]["sharpe_1y"]
    assert s is not None and s > 0


def test_beta_near_one_when_position_tracks_benchmark() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 1, "cost_basis": 100.0, "current_price": 120.0},
    ]
    # both move identically -> beta should be ~1
    history = {"AAPL": _days(252, start=100.0, step=0.08)}
    out = compute_cohort_stats(
        positions,
        {"AAPL": history["AAPL"]},
        {("USD", "equity_etf"): _days(252, start=100.0, step=0.08)},
        {("USD", "equity_etf"): 4.5},
    )
    b = out[0]["beta_1y"]
    assert b is not None and 0.9 < b < 1.1


def test_max_drawdown_negative_on_volatile_series() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    # price goes 100 → 150 → 50 → 80 (peak 150, trough 50 → drawdown ≈ -66%)
    h = [(date(2025, 5, 15) + timedelta(days=i), p)
         for i, p in enumerate([100] * 50 + [150] * 50 + [50] * 50 + [80] * 102)]
    positions = [
        {"ticker": "X", "currency": "USD", "asset_class": "equity",
         "quantity": 1, "cost_basis": 100.0, "current_price": 80.0},
    ]
    out = compute_cohort_stats(positions, {"X": h}, {}, {})
    dd = out[0]["max_drawdown_1y"]
    assert dd is not None and dd < -0.5  # at least -50%


def test_short_history_returns_none_for_sharpe_and_beta() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 1, "cost_basis": 100.0, "current_price": 110.0},
    ]
    short_history = {"AAPL": _days(30, start=100.0, step=0.5)}  # too few days
    out = compute_cohort_stats(positions, short_history, {}, {})
    # not enough data for sharpe/beta — they're None
    assert out[0]["sharpe_1y"] is None
    assert out[0]["beta_1y"] is None


def test_prices_partial_when_position_missing_current_price() -> None:
    from app.data.portfolio_stats import compute_cohort_stats
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "cost_basis": 100.0, "current_price": 110.0},
        {"ticker": "MSFT", "currency": "USD", "asset_class": "equity",
         "quantity": 5, "cost_basis": 400.0, "current_price": None},  # missing
    ]
    out = compute_cohort_stats(positions, {}, {}, {})
    assert out[0]["prices_partial"] is True
    # weights computed only on positions with prices
    assert "AAPL" in out[0]["weights"]
    assert "MSFT" not in out[0]["weights"]
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_portfolio_stats.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.portfolio_stats'`.

- [ ] **Step 3: Implement `portfolio_stats.py`**

Create `backend/app/data/portfolio_stats.py`:

```python
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from statistics import mean, pstdev
from typing import Any, Literal

CohortGroup = Literal["equity_etf", "crypto"]
CohortKey = tuple[str, CohortGroup]

_TRADING_DAYS = 252
_MIN_DAYS_FOR_RISK_METRICS = 100


def cohort_key(position: dict[str, Any]) -> CohortKey:
    """Return (currency, asset_class_group) for a position."""
    group: CohortGroup = "crypto" if position["asset_class"] == "crypto" else "equity_etf"
    return (position["currency"], group)


def _daily_returns(series: list[tuple[date, float]]) -> list[float]:
    """Daily log-returns from a sorted (date, close) series."""
    out: list[float] = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        cur = series[i][1]
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _align(
    a: list[tuple[date, float]], b: list[tuple[date, float]]
) -> tuple[list[float], list[float]]:
    """Inner-join two series by date and return their close values (in date order)."""
    bd = {d: v for d, v in b}
    a_aligned: list[float] = []
    b_aligned: list[float] = []
    for d, va in a:
        vb = bd.get(d)
        if vb is not None:
            a_aligned.append(va)
            b_aligned.append(vb)
    return a_aligned, b_aligned


def _max_drawdown(series: list[float]) -> float | None:
    """Max-drawdown (negative number) over a value series; None if too short."""
    if len(series) < 2:
        return None
    peak = series[0]
    max_dd = 0.0
    for v in series[1:]:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1.0
            if dd < max_dd:
                max_dd = dd
    return max_dd


def _lookback_return(series: list[tuple[date, float]], days: int) -> float | None:
    """Pct return of last value vs value `days` rows ago. Percent (×100). None if too short."""
    if len(series) <= days:
        return None
    past = series[-days - 1][1]
    now = series[-1][1]
    if past <= 0:
        return None
    return (now / past - 1.0) * 100.0


def _portfolio_value_series(
    positions: list[dict[str, Any]], price_history: dict[str, list[tuple[date, float]]]
) -> list[tuple[date, float]]:
    """For each date present in ALL position histories, sum qty * price."""
    common_dates: set[date] | None = None
    by_ticker: dict[str, dict[date, float]] = {}
    for p in positions:
        h = price_history.get(p["ticker"])
        if not h:
            continue
        d = {dt: v for dt, v in h}
        by_ticker[p["ticker"]] = d
        common_dates = set(d) if common_dates is None else (common_dates & set(d))
    if not common_dates or not by_ticker:
        return []
    out: list[tuple[date, float]] = []
    qty_by_ticker = {p["ticker"]: float(p["quantity"]) for p in positions}
    for d in sorted(common_dates):
        total = 0.0
        for ticker, day_map in by_ticker.items():
            total += qty_by_ticker[ticker] * day_map[d]
        out.append((d, total))
    return out


def compute_cohort_stats(
    positions: list[dict[str, Any]],
    price_history: dict[str, list[tuple[date, float]]],
    benchmark_history: dict[CohortKey, list[tuple[date, float]]],
    risk_free_rate: dict[CohortKey, float],
) -> list[dict[str, Any]]:
    """Return list of per-cohort stats dicts."""
    buckets: dict[CohortKey, list[dict[str, Any]]] = defaultdict(list)
    for p in positions:
        buckets[cohort_key(p)].append(p)

    out: list[dict[str, Any]] = []
    for key, positions_in_cohort in buckets.items():
        priced = [p for p in positions_in_cohort if p.get("current_price") is not None]
        prices_partial = len(priced) < len(positions_in_cohort)
        if not priced:
            out.append({
                "cohort": key,
                "positions_count": len(positions_in_cohort),
                "total_value_native": 0.0,
                "total_cost_native": sum(
                    float(p["cost_basis"] or 0) * float(p["quantity"])
                    for p in positions_in_cohort
                ),
                "gain_pct": None,
                "weights": {},
                "returns_1mo": None, "returns_3mo": None, "returns_1y": None,
                "benchmark_ticker": _benchmark_ticker_for(key),
                "benchmark_returns_1y": None,
                "sharpe_1y": None, "beta_1y": None, "max_drawdown_1y": None,
                "prices_partial": True,
            })
            continue

        value = sum(float(p["quantity"]) * float(p["current_price"]) for p in priced)
        cost = sum(float(p["quantity"]) * float(p["cost_basis"] or 0) for p in priced)
        weights = {
            p["ticker"]: (float(p["quantity"]) * float(p["current_price"])) / value
            for p in priced
        }
        gain_pct = (value - cost) / cost * 100.0 if cost > 0 else None

        port_series = _portfolio_value_series(priced, price_history)
        returns_1mo = _lookback_return(port_series, 21)
        returns_3mo = _lookback_return(port_series, 63)
        returns_1y = _lookback_return(port_series, _TRADING_DAYS)

        bench_series = benchmark_history.get(key, [])
        benchmark_returns_1y = _lookback_return(bench_series, _TRADING_DAYS)

        sharpe_1y: float | None = None
        beta_1y: float | None = None
        max_dd_1y: float | None = _max_drawdown([v for _, v in port_series])

        if len(port_series) >= _MIN_DAYS_FOR_RISK_METRICS:
            port_daily = _daily_returns(port_series)
            rf_annual = risk_free_rate.get(key, 0.0) / 100.0
            rf_daily = rf_annual / _TRADING_DAYS
            if port_daily and pstdev(port_daily) > 0:
                sharpe_1y = (
                    (mean(port_daily) - rf_daily)
                    / pstdev(port_daily)
                    * math.sqrt(_TRADING_DAYS)
                )

            if bench_series and len(bench_series) >= _MIN_DAYS_FOR_RISK_METRICS:
                port_a, bench_a = _align(port_series, bench_series)
                if len(port_a) >= _MIN_DAYS_FOR_RISK_METRICS:
                    port_a_daily = _daily_returns(list(zip(
                        [d for d, _ in port_series][: len(port_a)], port_a, strict=True,
                    )))
                    bench_a_daily = _daily_returns(list(zip(
                        [d for d, _ in port_series][: len(bench_a)], bench_a, strict=True,
                    )))
                    n = min(len(port_a_daily), len(bench_a_daily))
                    if n >= _MIN_DAYS_FOR_RISK_METRICS:
                        pa = port_a_daily[:n]
                        ba = bench_a_daily[:n]
                        mp = mean(pa)
                        mb = mean(ba)
                        cov = sum((pa[i] - mp) * (ba[i] - mb) for i in range(n)) / n
                        var_b = sum((ba[i] - mb) ** 2 for i in range(n)) / n
                        if var_b > 0:
                            beta_1y = cov / var_b

        out.append({
            "cohort": key,
            "positions_count": len(positions_in_cohort),
            "total_value_native": value,
            "total_cost_native": cost,
            "gain_pct": gain_pct,
            "weights": weights,
            "returns_1mo": returns_1mo,
            "returns_3mo": returns_3mo,
            "returns_1y": returns_1y,
            "benchmark_ticker": _benchmark_ticker_for(key),
            "benchmark_returns_1y": benchmark_returns_1y,
            "sharpe_1y": sharpe_1y,
            "beta_1y": beta_1y,
            "max_drawdown_1y": max_dd_1y,
            "prices_partial": prices_partial,
        })
    return out


def _benchmark_ticker_for(key: CohortKey) -> str:
    return {
        ("USD", "equity_etf"): "^GSPC",
        ("INR", "equity_etf"): "^NSEI",
        ("USD", "crypto"): "BTC-USD",
        ("INR", "crypto"): "BTC-USD",
    }.get(key, "^GSPC")
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_portfolio_stats.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 132 passing (123 prior + 9 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/data/portfolio_stats.py backend/tests/test_data_portfolio_stats.py && git commit -m "feat(backend): portfolio_stats pure math module — weights, returns, sharpe, beta, max drawdown

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Benchmark + FX adapters

**Files:**
- Create: `backend/app/data/benchmarks.py`
- Create: `backend/app/data/fx.py`
- Create: `backend/tests/test_data_benchmarks.py`
- Create: `backend/tests/test_data_fx.py`

**Goal:** Two thin async wrappers around `yfinance_adapter` + `cache_kv`. Both have 1h TTL (more aggressive than the 15-min price cache; benchmark/FX series barely move intraday). Each handles fetch failures gracefully (returns empty list / `None` rather than raising).

- [ ] **Step 1: Write failing tests for benchmarks**

Create `backend/tests/test_data_benchmarks.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Bar


def _sample_bars(n: int = 250) -> list[Bar]:
    d0 = date(2025, 5, 15)
    return [
        Bar(date=(d0 + timedelta(days=i)), open=100.0, high=100.0, low=100.0,
            close=100.0 + i, volume=0)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_get_benchmark_history_returns_usd_equity_series() -> None:
    bars = _sample_bars(250)
    fetch_mock = AsyncMock(return_value=bars)
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert len(series) == 250
    assert series[0][0].toordinal() < series[-1][0].toordinal()
    fetch_mock.assert_awaited_with("^GSPC", period="1y", interval="1d", market="US")


@pytest.mark.asyncio
async def test_get_benchmark_history_cache_hit_skips_fetch() -> None:
    now = datetime.now()
    cached_value = [
        {"date": "2025-05-15", "close": 100.0},
        {"date": "2025-05-16", "close": 101.0},
    ]
    rows = [{
        "key": "bench:^GSPC:1y",
        "value": cached_value,
        "expires_at": now + timedelta(minutes=30),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert len(series) == 2
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_benchmark_history_nse_for_inr_equity() -> None:
    fetch_mock = AsyncMock(return_value=_sample_bars(10))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        await get_benchmark_history(("INR", "equity_etf"))
    fetch_mock.assert_awaited_with("^NSEI", period="1y", interval="1d", market="IN")


@pytest.mark.asyncio
async def test_get_benchmark_history_btc_for_usd_crypto() -> None:
    fetch_mock = AsyncMock(return_value=_sample_bars(10))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        await get_benchmark_history(("USD", "crypto"))
    fetch_mock.assert_awaited_with("BTC-USD", period="1y", interval="1d", market="CRYPTO")


@pytest.mark.asyncio
async def test_get_benchmark_history_returns_empty_on_fetch_error() -> None:
    fetch_mock = AsyncMock(side_effect=RuntimeError("yfinance down"))
    with patch("app.data.benchmarks._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.benchmarks._write_cache_rows", AsyncMock()), \
         patch("app.data.benchmarks._fetch_price_history", fetch_mock):
        from app.data.benchmarks import get_benchmark_history
        series = await get_benchmark_history(("USD", "equity_etf"))
    assert series == []
```

Create `backend/tests/test_data_fx.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import TickerInfo


@pytest.mark.asyncio
async def test_get_usdinr_fresh_fetch() -> None:
    fake = TickerInfo(ticker="USDINR=X", name="USD/INR", currency="USD",
                      market_cap=None, last_price=83.50)
    fetch_mock = AsyncMock(return_value=fake)
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate == 83.50
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_usdinr_cache_hit() -> None:
    now = datetime.now()
    rows = [{
        "key": "fx:USDINR",
        "value": {"rate": 83.42, "as_of": now.isoformat()},
        "expires_at": now + timedelta(minutes=30),
    }]
    fetch_mock = AsyncMock()
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=rows)), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate == 83.42
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_usdinr_returns_none_on_error() -> None:
    fetch_mock = AsyncMock(side_effect=RuntimeError("yfinance down"))
    with patch("app.data.fx._fetch_cache_rows", AsyncMock(return_value=[])), \
         patch("app.data.fx._write_cache_rows", AsyncMock()), \
         patch("app.data.fx._fetch_ticker_info", fetch_mock):
        from app.data.fx import get_usdinr
        rate = await get_usdinr()
    assert rate is None
```

- [ ] **Step 2: Implement `benchmarks.py`**

Create `backend/app/data/benchmarks.py`:

```python
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history
from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=1)

CohortKey = tuple[str, str]

_BENCHMARK_TICKER: dict[CohortKey, tuple[str, str]] = {
    ("USD", "equity_etf"): ("^GSPC", "US"),
    ("INR", "equity_etf"): ("^NSEI", "IN"),
    ("USD", "crypto"): ("BTC-USD", "CRYPTO"),
    ("INR", "crypto"): ("BTC-USD", "CRYPTO"),
}


def _key(ticker: str) -> str:
    return f"bench:{ticker}:1y"


async def get_benchmark_history(cohort: CohortKey) -> list[tuple[date, float]]:
    """Return a 1y daily-close series for the cohort's benchmark. Empty list on failure."""
    ticker_market = _BENCHMARK_TICKER.get(cohort)
    if ticker_market is None:
        return []
    ticker, market = ticker_market
    rows = await _fetch_cache_rows([_key(ticker)])
    if rows:
        raw = rows[0]["value"]
        return [(date.fromisoformat(r["date"]), float(r["close"])) for r in raw]

    try:
        bars = await _fetch_price_history(ticker, period="1y", interval="1d", market=market)
    except Exception as e:  # noqa: BLE001
        logger.warning("benchmark fetch failed for %s — %s", ticker, e)
        return []

    series = [(b.date, float(b.close)) for b in bars]
    if not series:
        return []
    payload = [{"date": d.isoformat(), "close": v} for d, v in series]
    expires = datetime.now(UTC) + _TTL
    try:
        await _write_cache_rows([(_key(ticker), payload, expires)])  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001
        logger.warning("benchmark cache write failed: %s (not fatal)", e)
    return series
```

- [ ] **Step 3: Implement `fx.py`**

Create `backend/app/data/fx.py`:

```python
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.data.yfinance_adapter import fetch_ticker_info as _fetch_ticker_info
from app.db.prices import (
    fetch_cache_rows as _fetch_cache_rows,
    write_cache_rows as _write_cache_rows,
)

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=1)
_KEY = "fx:USDINR"


async def get_usdinr() -> float | None:
    """Live USDINR rate. 1h cache. None on fetch failure."""
    rows = await _fetch_cache_rows([_KEY])
    if rows:
        return float(rows[0]["value"]["rate"])

    try:
        info = await _fetch_ticker_info("USDINR=X", market="US")
    except Exception as e:  # noqa: BLE001
        logger.warning("USDINR fetch failed: %s", e)
        return None
    if info.last_price is None:
        return None
    rate = float(info.last_price)
    now = datetime.now(UTC)
    try:
        await _write_cache_rows([
            (_KEY, {"rate": rate, "as_of": now.isoformat()}, now + _TTL)
        ])
    except Exception as e:  # noqa: BLE001
        logger.warning("FX cache write failed: %s (not fatal)", e)
    return rate
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_benchmarks.py tests/test_data_fx.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 140 passing (132 prior + 8 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/data/benchmarks.py backend/app/data/fx.py backend/tests/test_data_benchmarks.py backend/tests/test_data_fx.py && git commit -m "feat(backend): benchmark + FX adapters (1h cache_kv TTL, graceful fallback)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Pydantic models + portfolio tools

**Files:**
- Create: `backend/app/models/portfolio_strategist.py`
- Create: `backend/app/tools/portfolio.py`
- Create: `backend/tests/test_tools_portfolio.py`

**Goal:** Define the 4 portfolio tools with **closure-bound user_id**. The tools are factories — call `build_portfolio_tools(user_id, portfolio_id=None)` to get a list of `Tool` instances whose `impl` closures capture those identifiers. The LLM cannot pass user_id; tool schemas don't expose it.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_tools_portfolio.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.mark.asyncio
async def test_get_holdings_uses_closure_user_id_not_args() -> None:
    """The LLM cannot pass user_id; it's captured at build time."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value=[{"id": UUID(PID)}])

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    get_holdings = next(t for t in tools if t.name == "get_holdings")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})):
        # LLM tries to pass user_id — should be ignored
        result = await get_holdings.impl(user_id="ATTACKER_UUID")

    # The actual SQL must use the closure user_id, not "ATTACKER_UUID"
    actual_args = conn.fetch.await_args.args
    assert UUID(USER_ID) in actual_args
    assert "ATTACKER_UUID" not in [str(a) for a in actual_args]
    assert result["holdings_count"] == 1


@pytest.mark.asyncio
async def test_get_holdings_returns_empty_when_user_has_no_portfolios() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)

    from app.tools.portfolio import build_portfolio_tools
    # No portfolio_id passed -> tool picks first portfolio; if none, returns empty
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=None)
    get_holdings = next(t for t in tools if t.name == "get_holdings")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})):
        result = await get_holdings.impl()

    assert result["holdings_count"] == 0
    assert result["portfolio_id"] is None


@pytest.mark.asyncio
async def test_calc_portfolio_stats_aggregates_per_cohort() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "BTC", "market": "CRYPTO",
         "asset_class": "crypto", "quantity": 0.5, "cost_basis": 40000.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from app.data.prices_cache import Price
    from datetime import datetime
    now = datetime(2026, 5, 15, 0, 0, 0)
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=120.0,
                              currency="USD", as_of=now),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80000.0,
                                 currency="USD", as_of=now),
    }

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    calc = next(t for t in tools if t.name == "calc_portfolio_stats")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.data.benchmarks.get_benchmark_history",
               AsyncMock(return_value=[])), \
         patch("app.tools.portfolio._fetch_price_history_for_position",
               AsyncMock(return_value=[])):
        result = await calc.impl()

    cohorts = result["cohorts"]
    assert len(cohorts) == 2
    keys = {tuple(c["cohort"]) for c in cohorts}
    assert ("USD", "equity_etf") in keys
    assert ("USD", "crypto") in keys


@pytest.mark.asyncio
async def test_suggest_rebalance_target_must_sum_to_one() -> None:
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    rebalance = next(t for t in tools if t.name == "suggest_rebalance")

    # invalid: sums to 0.9, not 1.0
    bad = {"USD:equity_etf": 0.5, "INR:equity_etf": 0.4}
    result = await rebalance.impl(target_alloc=bad)
    assert result["sum_check_ok"] is False


@pytest.mark.asyncio
async def test_suggest_rebalance_computes_deltas() -> None:
    """Single USD equity position; user wants 70/30 USD/INR. Should suggest reducing USD."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from app.data.prices_cache import Price
    from datetime import datetime
    now = datetime(2026, 5, 15)
    fake_prices = {("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                                          currency="USD", as_of=now)}

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    rebalance = next(t for t in tools if t.name == "suggest_rebalance")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.portfolio.get_usdinr", AsyncMock(return_value=83.0)):
        target = {"USD:equity_etf": 0.7, "INR:equity_etf": 0.3}
        result = await rebalance.impl(target_alloc=target)

    assert result["sum_check_ok"] is True
    # AAPL value is 10*200=2000 USD. Target = 70% in USD = 1400 USD,
    # 30% in INR = 600 USD eq = ~49800 INR. We need to REDUCE USD by 600 and
    # ADD INR by 600 USD equivalent (49800 INR).
    by_cohort = {tuple(t["cohort"]): t for t in result["cohort_trades"]}
    usd = by_cohort[("USD", "equity_etf")]
    assert usd["action"] == "decrease"
    assert abs(usd["delta_native"] - (-600.0)) < 1.0


@pytest.mark.asyncio
async def test_tax_lot_view_returns_virtual_lot_from_position() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    from app.data.prices_cache import Price
    from datetime import datetime
    fake_prices = {("AAPL", "US"): Price(ticker="AAPL", market="US", price=150.0,
                                          currency="USD", as_of=datetime(2026, 5, 15))}

    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id=USER_ID, portfolio_id=PID)
    tax_lot = next(t for t in tools if t.name == "tax_lot_view")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)):
        result = await tax_lot.impl()

    assert len(result["lots"]) == 1
    lot = result["lots"][0]
    assert lot["ticker"] == "AAPL"
    assert lot["qty"] == 10
    assert lot["basis"] == 100.0
    assert lot["current_value"] == 1500.0  # 10 * 150
    assert abs(lot["gain"] - 500.0) < 1e-6  # (150-100)*10
```

- [ ] **Step 2: Implement the models**

Create `backend/app/models/portfolio_strategist.py`:

```python
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

CohortGroup = Literal["equity_etf", "crypto"]


class PositionHint(BaseModel):
    ticker: str
    action: Literal["buy", "sell"]
    qty: float
    est_value_native: float


class RebalanceTrade(BaseModel):
    cohort: tuple[str, CohortGroup]
    action: Literal["increase", "decrease", "hold"]
    delta_native: float
    per_position_hints: list[PositionHint] = []


class Citation(BaseModel):
    source: str
    ref: str
```

- [ ] **Step 3: Implement the tools**

Create `backend/app/tools/portfolio.py`:

```python
from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Callable

from app.data.fx import get_usdinr
from app.data.portfolio_stats import compute_cohort_stats
from app.data.prices_cache import get_prices
from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history_for_position
from app.db import portfolios as db
from app.tools.base import Tool


async def _load_holdings(user_id: str, portfolio_id: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (resolved_portfolio_id, positions). If portfolio_id is None,
    auto-select the user's first portfolio. Returns (None, []) if no portfolios."""
    if portfolio_id is None:
        portfolios = await db.list_portfolios(user_id)
        if not portfolios:
            return None, []
        portfolio_id = str(portfolios[0]["id"])
    if not await db.assert_portfolio_owned(user_id, portfolio_id):
        return None, []
    positions = await db.list_positions(user_id, portfolio_id)
    return portfolio_id, positions


async def _holdings_with_prices(
    user_id: str, portfolio_id: str | None
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (portfolio_id, positions-with-current-price)."""
    pid, positions = await _load_holdings(user_id, portfolio_id)
    if not positions:
        return pid, []
    pairs = [(p["ticker"], p["market"]) for p in positions]
    prices = await get_prices(pairs)
    enriched: list[dict[str, Any]] = []
    for p in positions:
        price = prices.get((p["ticker"], p["market"]))
        enriched.append({**p, "current_price": price.price if price else None})
    return pid, enriched


def build_portfolio_tools(*, user_id: str, portfolio_id: str | None = None) -> list[Tool]:
    """Build a fresh set of 4 tools whose impls capture user_id + portfolio_id.

    The LLM-visible schemas DO NOT include user_id. Callers (the LLM) cannot
    influence which user's data is read.
    """

    async def _get_holdings(**_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        return {
            "portfolio_id": pid,
            "holdings_count": len(positions),
            "positions": [
                {
                    "ticker": p["ticker"], "market": p["market"],
                    "asset_class": p["asset_class"],
                    "quantity": float(p["quantity"]),
                    "cost_basis": float(p["cost_basis"]) if p["cost_basis"] is not None else None,
                    "currency": p["currency"],
                    "opened_at": p["opened_at"].isoformat() if p.get("opened_at") else None,
                    "current_price": p["current_price"],
                }
                for p in positions
            ],
        }

    async def _calc_portfolio_stats(**_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "cohorts": []}
        cohort_keys = {(p["currency"], "crypto" if p["asset_class"] == "crypto" else "equity_etf")
                       for p in positions}
        from app.data.benchmarks import get_benchmark_history
        benchmark_history: dict[tuple[str, str], list[tuple[date_cls, float]]] = {}
        for key in cohort_keys:
            benchmark_history[key] = await get_benchmark_history(key)
        price_history: dict[str, list[tuple[date_cls, float]]] = {}
        for p in positions:
            try:
                bars = await _fetch_price_history_for_position(
                    p["ticker"], period="1y", interval="1d", market=p["market"],
                )
            except Exception:
                bars = []
            price_history[p["ticker"]] = [(b.date, float(b.close)) for b in bars]
        risk_free: dict[tuple[str, str], float] = {
            ("USD", "equity_etf"): 4.5, ("USD", "crypto"): 4.5,
            ("INR", "equity_etf"): 6.5, ("INR", "crypto"): 4.5,
        }
        cohorts = compute_cohort_stats(positions, price_history, benchmark_history, risk_free)
        return {"portfolio_id": pid, "cohorts": cohorts}

    async def _suggest_rebalance(
        target_alloc: dict[str, float] | None = None,
        mode: str = "cross_cohort_fx",
        suggest_per_position: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not target_alloc:
            return {"sum_check_ok": False, "message": "target_alloc is required"}
        s = sum(target_alloc.values())
        sum_check_ok = abs(s - 1.0) < 0.001
        if not sum_check_ok:
            return {"sum_check_ok": False,
                    "message": f"target_alloc sums to {s:.3f}, must sum to 1.0"}

        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"sum_check_ok": True, "portfolio_id": pid, "cohort_trades": []}

        # Bucket by cohort
        from collections import defaultdict
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for p in positions:
            group = "crypto" if p["asset_class"] == "crypto" else "equity_etf"
            buckets[(p["currency"], group)].append(p)

        # Cohort values (native)
        cohort_value_native: dict[tuple[str, str], float] = {}
        for key, ps in buckets.items():
            cohort_value_native[key] = sum(
                float(p["quantity"]) * float(p["current_price"] or 0)
                for p in ps
            )

        # Optional FX comparison
        usdinr: float | None = None
        comparison_currency = "native_only"
        if mode == "cross_cohort_fx":
            usdinr = await get_usdinr()
            if usdinr is not None:
                comparison_currency = "USD"

        # Convert each cohort to USD for the gap math
        cohort_value_usd: dict[tuple[str, str], float] = {}
        for key, value in cohort_value_native.items():
            if comparison_currency == "USD":
                cohort_value_usd[key] = value / usdinr if key[0] == "INR" else value  # type: ignore[operator]
            else:
                cohort_value_usd[key] = value  # treat as native, no cross-currency

        total_usd = sum(cohort_value_usd.values()) or 1.0
        current_alloc_pct = {
            f"{k[0]}:{k[1]}": cohort_value_usd[k] / total_usd
            for k in cohort_value_usd
        }

        trades: list[dict[str, Any]] = []
        for target_key, target_pct in target_alloc.items():
            currency, group = target_key.split(":", 1)
            key = (currency, group)
            current_usd = cohort_value_usd.get(key, 0.0)
            target_usd = total_usd * target_pct
            delta_usd = target_usd - current_usd
            if comparison_currency == "USD" and currency == "INR" and usdinr:
                delta_native = delta_usd * usdinr
            else:
                delta_native = delta_usd
            action = "hold" if abs(delta_native) < 0.01 * (cohort_value_native.get(key, 1.0) or 1.0) else (
                "increase" if delta_native > 0 else "decrease"
            )

            hints: list[dict[str, Any]] = []
            if suggest_per_position and key in buckets and abs(delta_native) > 0:
                ps_in_cohort = buckets[key]
                cohort_value = cohort_value_native.get(key, 0.0)
                if cohort_value > 0:
                    for p in ps_in_cohort:
                        pos_value = float(p["quantity"]) * float(p["current_price"] or 0)
                        weight = pos_value / cohort_value
                        pos_delta = delta_native * weight
                        if abs(pos_delta) < 0.01:
                            continue
                        # Cap at 50% of current position
                        cap = pos_value * 0.5
                        capped = max(-cap, min(cap, pos_delta))
                        qty_change = capped / float(p["current_price"]) if p["current_price"] else 0.0
                        hints.append({
                            "ticker": p["ticker"],
                            "action": "buy" if capped > 0 else "sell",
                            "qty": abs(qty_change),
                            "est_value_native": abs(capped),
                        })

            trades.append({
                "cohort": list(key),
                "action": action,
                "delta_native": delta_native,
                "per_position_hints": hints,
            })

        return {
            "sum_check_ok": True,
            "portfolio_id": pid,
            "target_alloc": target_alloc,
            "comparison_currency": comparison_currency,
            "assumed_fx_rates": {"USDINR": usdinr} if usdinr is not None else {},
            "current_alloc_pct": current_alloc_pct,
            "cohort_trades": trades,
        }

    async def _tax_lot_view(ticker: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "lots": []}
        if ticker:
            positions = [p for p in positions if p["ticker"].upper() == ticker.upper()]
        lots: list[dict[str, Any]] = []
        for p in positions:
            qty = float(p["quantity"])
            basis = float(p["cost_basis"] or 0)
            price = float(p["current_price"] or 0)
            lots.append({
                "ticker": p["ticker"],
                "qty": qty,
                "basis": basis,
                "current_value": qty * price if price else 0.0,
                "gain": (price - basis) * qty if price else None,
                "currency": p["currency"],
                "acquired_at": p["opened_at"].isoformat() if p.get("opened_at") else None,
                "virtual": True,
            })
        return {"portfolio_id": pid, "lots": lots}

    return [
        Tool(
            name="get_holdings",
            description=(
                "Return the user's current holdings for the active portfolio. "
                "Each position includes ticker, market, asset_class, quantity, "
                "cost_basis, currency, opened_at, and live current_price."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_get_holdings,
        ),
        Tool(
            name="calc_portfolio_stats",
            description=(
                "Compute per-cohort statistics: weights, returns (1mo/3mo/1y), "
                "benchmark comparison, sharpe, beta, max drawdown. Cohorts are "
                "(currency, asset_class_group). USD-equity and USD-crypto are SEPARATE."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_calc_portfolio_stats,
        ),
        Tool(
            name="suggest_rebalance",
            description=(
                "Given a target_alloc dict (cohort_key -> fraction, must sum to 1.0), "
                "compute the gap and suggest trades in each cohort's native currency. "
                "mode='cross_cohort_fx' uses USDINR for comparison; 'per_cohort_only' "
                "treats each cohort independently."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "target_alloc": {
                        "type": "object",
                        "description": "cohort_key (e.g. 'USD:equity_etf') -> fraction",
                        "additionalProperties": {"type": "number"},
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["cross_cohort_fx", "per_cohort_only"],
                        "default": "cross_cohort_fx",
                    },
                    "suggest_per_position": {"type": "boolean", "default": True},
                },
                "required": ["target_alloc"],
            },
            impl=_suggest_rebalance,
        ),
        Tool(
            name="tax_lot_view",
            description=(
                "Return tax-lot view per position. In Phase 3B the tax_lots table is "
                "unused so each position appears as one virtual lot (qty/basis/opened_at). "
                "Optional ticker filter."
            ),
            input_schema={
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": [],
            },
            impl=_tax_lot_view,
        ),
    ]
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_tools_portfolio.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 146 passing (140 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/models/portfolio_strategist.py backend/app/tools/portfolio.py backend/tests/test_tools_portfolio.py && git commit -m "feat(backend): portfolio tools (closure-bound user_id, 4 tools)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Portfolio Strategist agent

**Files:**
- Create: `backend/app/agents/portfolio_strategist.py`
- Create: `backend/app/prompts/portfolio_strategist.md`
- Create: `backend/tests/test_portfolio_strategist_agent.py`

**Goal:** A Sonnet agent that takes `user_id`, `portfolio_id?`, `brief`, optional `target_alloc`, runs a tool loop, and returns a typed `PortfolioFindings`. Pattern matches Phase 2 specialists (Fundamental/Technical/News/Macro).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_portfolio_strategist_agent.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*content_blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = content_blocks
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_strategist_dispatches_get_holdings_then_submits() -> None:
    """LLM calls get_holdings, then submit_portfolio_findings; agent returns findings."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id="p-1")
    # Patch impls so we don't hit the DB
    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "holdings_count": 1,
                                              "positions": [{"ticker": "AAPL"}]})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "My Portfolio",
                "cohorts": [],
                "rebalance": None,
                "notes": ["empty cohort list (no positions)"],
                "citations": [],
                "confidence": 0.7,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    findings = await run_portfolio_strategist(
        user_id="u-1", portfolio_id="p-1",
        brief="snapshot",
        target_alloc=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["portfolio_id"] == "p-1"
    assert findings["confidence"] == 0.7


@pytest.mark.asyncio
async def test_strategist_short_circuits_when_no_portfolios() -> None:
    """If get_holdings returns 0 holdings AND no portfolio_id, agent emits the empty path."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id=None)
    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": None, "holdings_count": 0,
                                              "positions": []})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": None,
                "portfolio_name": "",
                "cohorts": [],
                "rebalance": None,
                "notes": ["You don't have a portfolio yet. Visit /portfolio to create one."],
                "citations": [],
                "confidence": 1.0,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    findings = await run_portfolio_strategist(
        user_id="u-1", portfolio_id=None,
        brief="snapshot",
        target_alloc=None,
        client=fake_client,
        tools_override=tools,
    )
    assert findings["portfolio_id"] is None
    assert "create one" in findings["notes"][0]


@pytest.mark.asyncio
async def test_strategist_passes_target_alloc_to_rebalance_tool() -> None:
    """When brief mentions rebalance + target_alloc passed in, the agent's calls reach
    suggest_rebalance with the target dict."""
    from app.tools.portfolio import build_portfolio_tools
    tools = build_portfolio_tools(user_id="u-1", portfolio_id="p-1")
    rebalance_calls: list[dict[str, Any]] = []

    for t in tools:
        if t.name == "get_holdings":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "holdings_count": 1,
                                              "positions": [{"ticker": "AAPL"}]})
        elif t.name == "suggest_rebalance":
            async def _capture(**kwargs: Any) -> dict[str, Any]:
                rebalance_calls.append(kwargs)
                return {"sum_check_ok": True, "cohort_trades": []}
            t.impl = _capture
        else:
            t.impl = AsyncMock(return_value={})

    target = {"USD:equity_etf": 0.6, "INR:equity_etf": 0.4}
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("get_holdings", {}, "g1")),
        _message(_tool_use("suggest_rebalance", {"target_alloc": target}, "r1")),
        _message(
            _tool_use("submit_portfolio_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "My Portfolio",
                "cohorts": [],
                "rebalance": None,
                "notes": [],
                "citations": [],
                "confidence": 0.8,
            }, "s1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.portfolio_strategist import run_portfolio_strategist
    await run_portfolio_strategist(
        user_id="u-1", portfolio_id="p-1",
        brief="rebalance to 60/40 USD/INR",
        target_alloc=target,
        client=fake_client,
        tools_override=tools,
    )
    assert len(rebalance_calls) == 1
    assert rebalance_calls[0]["target_alloc"] == target
```

- [ ] **Step 2: Implement the prompt**

Create `backend/app/prompts/portfolio_strategist.md`:

```markdown
You are the Portfolio Strategist inside Stylobate. Your job: analyze the user's holdings and surface clear, useful insights.

You have 4 tools:

- `get_holdings()` — current holdings with live prices. Call this FIRST in every run.
- `calc_portfolio_stats()` — per-cohort weights, returns (1mo/3mo/1y), benchmark comparison, sharpe, beta, max drawdown.
- `suggest_rebalance(target_alloc, mode?, suggest_per_position?)` — only call if the user asks for rebalancing AND you have a target. target_alloc must sum to 1.0. cohort keys look like "USD:equity_etf", "INR:equity_etf", "USD:crypto".
- `tax_lot_view(ticker?)` — virtual-lot view per position. In 3B, each position is one virtual lot.

Process:

1. ALWAYS call `get_holdings` first.
2. If `holdings_count == 0`: call `submit_portfolio_findings` immediately with `notes=["You don't have a portfolio yet. Visit /portfolio to create one."]` and stop.
3. Otherwise call `calc_portfolio_stats` to get cohort breakdown.
4. If the user asked for rebalance AND a target_alloc was provided OR you can confidently parse one from the brief: call `suggest_rebalance(target_alloc=...)`.
5. Optionally call `tax_lot_view(ticker=X)` if the user asked about a specific position's gain.
6. Call `submit_portfolio_findings` exactly once with everything you've learned.

Discipline:

- Cohort keys are STRICTLY `(currency, asset_class_group)`. USD-equity and USD-crypto are SEPARATE cohorts.
- Never claim to convert currencies in aggregates. If you cite USD totals across cohorts, it's only via the rebalance comparison and you must say "USD-equivalent (USDINR=X.YY)".
- `notes` is a list of short bullets surfacing edge cases: empty cohort, missing prices ("4/5 priced"), assumed FX rate, etc.
- `citations` reference data sources: "yfinance" for prices/benchmarks, "FRED" for risk-free rates.
- `confidence`: 0.85+ when you have complete data; 0.6-0.8 with prices_partial or short history; <0.6 if many gaps.
- DO NOT recommend specific securities or assert what the user "should" do. State the gap and the math; let the user decide.
```

- [ ] **Step 3: Implement the agent**

Create `backend/app/agents/portfolio_strategist.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.portfolio import build_portfolio_tools

_SUBMIT_TOOL = Tool(
    name="submit_portfolio_findings",
    description="Submit the final portfolio analysis. Call exactly once at the end.",
    input_schema={
        "type": "object",
        "properties": {
            "portfolio_id": {"type": ["string", "null"]},
            "portfolio_name": {"type": "string"},
            "cohorts": {"type": "array"},
            "rebalance": {"type": ["object", "null"]},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": ["portfolio_id", "portfolio_name", "cohorts", "notes", "confidence"],
    },
    impl=None,  # marker tool; handled inline
)


def _load_system_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "portfolio_strategist.md"
    return path.read_text(encoding="utf-8")


async def run_portfolio_strategist(
    *,
    user_id: str,
    portfolio_id: str | None,
    brief: str,
    target_alloc: dict[str, float] | None = None,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """Run the Portfolio Strategist Sonnet agent. Returns a findings dict.

    user_id is closure-scoped into the tools — never passed to the LLM.
    """
    c = client or get_client()
    tools = tools_override or build_portfolio_tools(user_id=user_id, portfolio_id=portfolio_id)
    # Append the submit tool
    all_tools = [*tools, _SUBMIT_TOOL]
    tool_schemas = [{"name": t.name, "description": t.description,
                     "input_schema": t.input_schema} for t in all_tools]
    tools_by_name = {t.name: t for t in tools}

    user_content = (
        f"Brief: {brief}\n"
        f"Portfolio ID: {portfolio_id or '(default: first)'}\n"
    )
    if target_alloc:
        user_content += f"target_alloc: {json.dumps(target_alloc)}\n"
    user_content += (
        "\nUse the tools to read holdings, compute stats, optionally suggest "
        "rebalance, then submit_portfolio_findings."
    )

    messages: list[MessageParam] = [{"role": "user", "content": user_content}]

    findings: dict[str, Any] | None = None
    for _turn in range(8):  # bounded loop
        resp = await c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_load_system_prompt(),
            tools=tool_schemas,
            messages=messages,
        )
        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            name = block["name"] if isinstance(block, dict) else block.name
            block_id = block["id"] if isinstance(block, dict) else block.id
            args = block["input"] if isinstance(block, dict) else block.input
            if name == "submit_portfolio_findings":
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
        messages.append({"role": "assistant", "content": resp.content})  # type: ignore[arg-type]
        messages.append({"role": "user", "content": tool_results})  # type: ignore[arg-type]

    if findings is None:
        findings = {
            "portfolio_id": portfolio_id,
            "portfolio_name": "",
            "cohorts": [],
            "rebalance": None,
            "notes": ["Agent did not submit findings within turn budget."],
            "citations": [],
            "confidence": 0.0,
        }
    return findings
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_portfolio_strategist_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 149 passing (146 prior + 3 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/agents/portfolio_strategist.py backend/app/prompts/portfolio_strategist.md backend/tests/test_portfolio_strategist_agent.py && git commit -m "feat(backend): Portfolio Strategist Sonnet agent (8-turn loop, closure-bound user_id)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `/chat/portfolio` direct endpoint

**Files:**
- Create: `backend/app/routes/chat_portfolio.py`
- Create: `backend/tests/test_routes_chat_portfolio_endpoint.py`
- Modify: `backend/app/main.py` (register router)

**Goal:** Dedicated POST endpoint that goes straight to the strategist (no Lead Banker). Streams SSE with the same event shape as `/chat/stream`: progress / delta(section) / done. The frontend doesn't yet call this in 3B; it remains a clean API surface for future tooling.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_routes_chat_portfolio_endpoint.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_chat_portfolio_streams_sections(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    fake_findings = {
        "portfolio_id": "00000000-0000-0000-0000-000000000001",
        "portfolio_name": "My Portfolio",
        "cohorts": [
            {"cohort": ["USD", "equity_etf"], "positions_count": 1,
             "total_value_native": 2000.0, "total_cost_native": 1500.0,
             "gain_pct": 33.3, "weights": {"AAPL": 1.0},
             "returns_1mo": 5.0, "returns_3mo": 12.0, "returns_1y": 30.0,
             "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 18.0,
             "sharpe_1y": 1.2, "beta_1y": 1.05, "max_drawdown_1y": -0.08,
             "prices_partial": False},
        ],
        "rebalance": None,
        "notes": [],
        "citations": [{"source": "yfinance", "ref": "AAPL 1y"}],
        "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_findings),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "How am I doing?"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "event: delta" in body
    assert "Portfolio Snapshot" in body or "snapshot" in body.lower()
    assert "AAPL" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/portfolio", json={"message": "hi"})
    assert r.status_code == 401
```

- [ ] **Step 2: Implement the endpoint**

Create `backend/app/routes/chat_portfolio.py`:

```python
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.portfolio_strategist import run_portfolio_strategist
from app.core.auth import get_current_user

router = APIRouter(tags=["chat-portfolio"])


class ChatPortfolioRequest(BaseModel):
    portfolio_id: str | None = None
    message: str | None = None
    target_alloc: dict[str, float] | None = None


def _format_currency(value: float, currency: str) -> str:
    sym = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(currency, f"{currency} ")
    return f"{sym}{value:,.0f}"


def _cohort_label(cohort: list[str]) -> str:
    currency, group = cohort[0], cohort[1]
    return f"{currency} ({'Crypto' if group == 'crypto' else 'Equities & ETFs'})"


def _render_snapshot_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])
        currency = c["cohort"][0]
        value = _format_currency(c["total_value_native"], currency)
        gain = f"{c['gain_pct']:+.1f}%" if c.get("gain_pct") is not None else "—"
        lines.append(f"- **{label}**: {value} ({gain}, {c['positions_count']} positions)")
        weights_top = sorted(c.get("weights", {}).items(), key=lambda x: -x[1])[:5]
        if weights_top:
            wts = ", ".join(f"{t} {w * 100:.1f}%" for t, w in weights_top)
            lines.append(f"  - weights: {wts}")
    return "\n".join(lines) or "_No positions._"


def _render_returns_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])

        def fmt(v: float | None) -> str:
            return f"{v:+.1f}%" if v is not None else "—"

        lines.append(
            f"- **{label}** — 1mo {fmt(c.get('returns_1mo'))}, "
            f"3mo {fmt(c.get('returns_3mo'))}, "
            f"1y {fmt(c.get('returns_1y'))} "
            f"vs benchmark {c.get('benchmark_ticker')}: "
            f"1y {fmt(c.get('benchmark_returns_1y'))}"
        )
    return "\n".join(lines) or "_Returns unavailable._"


def _render_risk_section(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cohorts:
        label = _cohort_label(c["cohort"])

        def fmt(v: float | None, digits: int = 2) -> str:
            return f"{v:.{digits}f}" if v is not None else "—"

        dd = c.get("max_drawdown_1y")
        dd_pct = f"{dd * 100:+.1f}%" if dd is not None else "—"
        lines.append(
            f"- **{label}** — sharpe {fmt(c.get('sharpe_1y'))}, "
            f"beta {fmt(c.get('beta_1y'))}, max drawdown {dd_pct}"
        )
    return "\n".join(lines) or "_Risk metrics unavailable._"


def _render_rebalance_section(rebalance: dict[str, Any]) -> str:
    if not rebalance or not rebalance.get("sum_check_ok"):
        return rebalance.get("message", "No rebalance computed.") if rebalance else ""
    lines = [
        f"_Comparison currency: {rebalance.get('comparison_currency', 'native_only')}_",
    ]
    rates = rebalance.get("assumed_fx_rates") or {}
    if rates:
        lines.append(f"_Assumed FX: USDINR = {rates.get('USDINR'):.2f}_")
    for t in rebalance.get("cohort_trades", []):
        cohort = t["cohort"]
        currency = cohort[0]
        delta = t["delta_native"]
        action = t["action"]
        if action == "hold":
            lines.append(f"- **{_cohort_label(cohort)}**: hold (no change needed)")
        else:
            verb = "Add" if action == "increase" else "Reduce"
            amount = _format_currency(abs(delta), currency)
            lines.append(f"- **{_cohort_label(cohort)}**: {verb} {amount}")
    return "\n".join(lines)


async def _stream_findings(findings: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield SSE events: progress, delta(section)*, done."""
    yield b"event: progress\ndata: " + json.dumps({"step": "analyzing_portfolio"}).encode() + b"\n\n"

    cohorts = findings.get("cohorts", [])
    if not cohorts:
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Portfolio",
                          "markdown": findings.get("notes", ["No portfolio data."])[0],
                          "citations": []}).encode()
            + b"\n\n"
        )
    else:
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Portfolio Snapshot",
                          "markdown": _render_snapshot_section(cohorts),
                          "citations": []}).encode()
            + b"\n\n"
        )
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Returns vs Benchmark",
                          "markdown": _render_returns_section(cohorts),
                          "citations": []}).encode()
            + b"\n\n"
        )
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Risk Metrics",
                          "markdown": _render_risk_section(cohorts),
                          "citations": []}).encode()
            + b"\n\n"
        )
        if findings.get("rebalance"):
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Rebalance Plan",
                              "markdown": _render_rebalance_section(findings["rebalance"]),
                              "citations": []}).encode()
                + b"\n\n"
            )
    if findings.get("notes"):
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Notes",
                          "markdown": "\n".join(f"- {n}" for n in findings["notes"]),
                          "citations": []}).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"


@router.post("/chat/portfolio")
async def chat_portfolio(
    req: ChatPortfolioRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    findings = await run_portfolio_strategist(
        user_id=user["sub"],
        portfolio_id=req.portfolio_id,
        brief=req.message or "Snapshot of my current portfolio.",
        target_alloc=req.target_alloc,
    )
    return StreamingResponse(_stream_findings(findings), media_type="text/event-stream")
```

- [ ] **Step 3: Register router**

In `backend/app/main.py`, add:

```python
from app.routes import chat_portfolio as chat_portfolio_routes
app.include_router(chat_portfolio_routes.router)
```

(Insert near the other `include_router` calls inside `create_app()`.)

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_portfolio_endpoint.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 151 passing (149 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/chat_portfolio.py backend/app/main.py backend/tests/test_routes_chat_portfolio_endpoint.py && git commit -m "feat(backend): /chat/portfolio direct endpoint (SSE; section renderers)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Lead Banker integration — `dispatch_portfolio_strategist` + `portfolio_mode`

**Files:**
- Modify: `backend/app/agents/lead_banker.py`
- Modify: `backend/app/prompts/lead_banker.md`
- Create: `backend/tests/test_lead_banker_portfolio_mode.py`

**Goal:** Lead Banker gets a new tool `dispatch_portfolio_strategist` that runs the Portfolio Strategist agent. When called with `portfolio_mode=True`, Lead Banker's prompt steers it to use this new tool instead of `dispatch_specialists`. Phase 2 specialists are NOT dispatched in portfolio_mode.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_lead_banker_portfolio_mode.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool_use(name: str, args: dict[str, Any], tool_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _message(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_lead_banker_portfolio_mode_calls_strategist_not_specialists() -> None:
    """In portfolio_mode, Lead Banker uses dispatch_portfolio_strategist, NOT dispatch_specialists."""
    captured_findings_call: list[dict[str, Any]] = []

    async def fake_strategist(**kwargs: Any) -> dict[str, Any]:
        captured_findings_call.append(kwargs)
        return {
            "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
            "cohorts": [
                {"cohort": ["USD", "equity_etf"], "positions_count": 1,
                 "total_value_native": 2000.0, "total_cost_native": 1500.0,
                 "gain_pct": 33.3, "weights": {"AAPL": 1.0},
                 "returns_1mo": 5.0, "returns_3mo": 12.0, "returns_1y": 30.0,
                 "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 18.0,
                 "sharpe_1y": 1.2, "beta_1y": 1.05, "max_drawdown_1y": -0.08,
                 "prices_partial": False},
            ],
            "rebalance": None, "notes": [], "citations": [], "confidence": 0.85,
        }

    fake_client = MagicMock()
    # Lead Banker turn 1: dispatch_portfolio_strategist
    # Lead Banker turn 2: emit_* tools, end_turn
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_strategist",
                           {"brief": "Snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tool_use("emit_section", {"title": "Portfolio Snapshot",
                                        "markdown": "- AAPL 100%", "citations": []}, "2"),
            _tool_use("emit_section", {"title": "Risks", "markdown": "Concentrated.",
                                        "citations": []}, "3"),
            _tool_use("emit_disclaimer", {}, "4"),
            _tool_use("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)

        deltas: list[dict[str, Any]] = []
        async for d in lb.run_lead_banker(
            user_message="How is my portfolio doing?",
            resolution=None,
            client=fake_client,
            portfolio_mode=True,
            user_id="u-1",
        ):
            deltas.append(d)

    # Strategist was called exactly once
    assert len(captured_findings_call) == 1
    assert captured_findings_call[0]["user_id"] == "u-1"
    # Final stream contains the emitted sections
    section_titles = [d.get("title") for d in deltas if d.get("type") == "section"]
    assert "Portfolio Snapshot" in section_titles
```

- [ ] **Step 2: Update Lead Banker — add tool**

In `backend/app/agents/lead_banker.py`, add this near the top with the other imports:

```python
from app.agents.portfolio_strategist import run_portfolio_strategist
```

Find the `dispatch_specialists` tool definition (likely a `Tool(name="dispatch_specialists", ...)`). After it, add:

```python
dispatch_portfolio_strategist_tool = Tool(
    name="dispatch_portfolio_strategist",
    description=(
        "Run the Portfolio Strategist on the user's current portfolio. "
        "Use this ONLY when portfolio_mode is set. Pass a brief describing what "
        "they want (snapshot, rebalance, comparison). If they specified a target "
        "allocation, pass it as target_alloc dict."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "brief": {"type": "string"},
            "target_alloc": {
                "type": "object",
                "description": "cohort_key (e.g. 'USD:equity_etf') -> fraction",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["brief"],
    },
    impl=None,  # handled inline
)
```

Find the `run_lead_banker` signature and add `portfolio_mode: bool = False` + `user_id: str | None = None`:

```python
async def run_lead_banker(
    *,
    user_message: str,
    resolution: TickerResolution | None,
    client: AsyncAnthropic | Any | None = None,
    portfolio_mode: bool = False,
    user_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
```

Update the `tools` list used by the LLM:

```python
all_tools = [...existing emit_* tools..., dispatch_specialists_tool]
if portfolio_mode:
    all_tools.append(dispatch_portfolio_strategist_tool)
```

Update the user message construction so when `portfolio_mode=True`, the message says so and DOESN'T include a ticker:

```python
if portfolio_mode:
    user_content = (
        f"User question: {user_message}\n\n"
        "Portfolio mode is active — analyze the user's portfolio. "
        "Call dispatch_portfolio_strategist exactly once with a brief, "
        "then emit_* the response. Emit order: quick_take → "
        "stock_card (skip — no single ticker) → sections → recommendation "
        "(only if rebalance was requested) → disclaimer → done.\n\n"
        "Do NOT call dispatch_specialists in portfolio mode."
    )
else:
    user_content = _build_user_message(user_message, resolution)  # existing
```

In the tool-dispatch loop, add a branch for `dispatch_portfolio_strategist`:

```python
elif name == "dispatch_portfolio_strategist":
    if not portfolio_mode or user_id is None:
        result = {"error": "portfolio_mode not active or user_id missing"}
    else:
        findings = await run_portfolio_strategist(
            user_id=user_id,
            portfolio_id=None,
            brief=args.get("brief", "Analyze portfolio."),
            target_alloc=args.get("target_alloc"),
            client=client,
        )
        result = findings
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": json.dumps(result, default=str),
    })
    continue
```

- [ ] **Step 3: Update Lead Banker prompt**

Append to `backend/app/prompts/lead_banker.md`:

```markdown

## Portfolio mode

If the user message states "Portfolio mode is active", you have one additional tool: `dispatch_portfolio_strategist`. Use it INSTEAD of `dispatch_specialists` (do not call dispatch_specialists in portfolio mode).

Workflow in portfolio mode:

1. Call `dispatch_portfolio_strategist` with a `brief` and, if the user stated a target allocation, `target_alloc` as a dict like `{"USD:equity_etf": 0.5, "INR:equity_etf": 0.3, "USD:crypto": 0.2}` (must sum to 1.0). cohort keys are strictly `(currency, asset_class_group)`.
2. The tool returns a `PortfolioFindings`-shape result with `cohorts`, `rebalance` (if you asked for one), and `notes`.
3. Synthesize into `emit_*` tools in this order: `emit_quick_take` → `emit_section` (Portfolio Snapshot) → `emit_section` (Returns vs Benchmark) → `emit_section` (Risk Metrics) → `emit_section` (Rebalance Plan — only if the user asked) → `emit_section` (Risks — concentration, missing prices, etc.) → `emit_recommendation` (only if rebalance requested; signal: "hold" by default) → `emit_disclaimer` → `emit_done`.

Discipline in portfolio mode:
- Do NOT emit `emit_stock_card` — there's no single ticker.
- Cohorts are SEPARATE: USD-equity and USD-crypto are NOT the same cohort even though both quote in USD.
- Never claim to convert cohorts in the snapshot. Cross-cohort comparison is only valid inside the rebalance plan, where the assumed FX rate is stated.
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_lead_banker_portfolio_mode.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 152 passing (151 prior + 1 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/agents/lead_banker.py backend/app/prompts/lead_banker.md backend/tests/test_lead_banker_portfolio_mode.py && git commit -m "feat(backend): Lead Banker portfolio_mode — dispatch_portfolio_strategist tool

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `/chat/stream` keyword detection

**Files:**
- Modify: `backend/app/routes/chat.py`
- Create: `backend/tests/test_routes_chat_portfolio_mode.py`

**Goal:** Add a keyword check at the top of `/chat/stream`. When matched, skip ticker resolution and run Lead Banker in `portfolio_mode=True`. When NOT matched, the existing ticker path runs unchanged.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_routes_chat_portfolio_mode.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.routes.chat import is_portfolio_query


TEST_USER_ID = "00000000-0000-0000-0000-000000000099"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_is_portfolio_query_matches_common_phrasings() -> None:
    assert is_portfolio_query("how is my portfolio doing?")
    assert is_portfolio_query("rebalance my holdings")
    assert is_portfolio_query("review my allocation please")
    assert is_portfolio_query("am I too concentrated?")
    assert is_portfolio_query("how am I doing on investments")
    assert not is_portfolio_query("what's AAPL doing today")
    assert not is_portfolio_query("Is Tesla a buy?")


@pytest.mark.asyncio
async def test_chat_stream_portfolio_query_skips_ticker_resolution(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """A portfolio query should NOT call resolve_ticker; it should run Lead Banker in portfolio_mode."""
    resolve_mock = AsyncMock()  # should not be called

    async def fake_lead_banker(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "progress", "step": "running_portfolio_strategist"}
        yield {"type": "section", "title": "Portfolio Snapshot",
               "markdown": "- USD equity 100%", "citations": []}
        yield {"type": "done"}

    with patch("app.routes.chat.resolve_ticker", resolve_mock), \
         patch("app.routes.chat.run_lead_banker", fake_lead_banker):
        r = await client.post(
            "/chat/stream",
            json={"content": "How is my portfolio doing?"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    resolve_mock.assert_not_called()
    assert "Portfolio Snapshot" in body
```

- [ ] **Step 2: Implement keyword detection in `chat.py`**

In `backend/app/routes/chat.py`, add at module scope (near top, after imports):

```python
PORTFOLIO_KEYWORDS = (
    "my portfolio", "my holdings", "my positions",
    "rebalance", "allocation", "asset mix",
    "how am i doing", "how's my", "review my",
    "diversif", "concentration",
)


def is_portfolio_query(text: str) -> bool:
    """Return True if the message looks like a portfolio question."""
    t = text.lower()
    return any(kw in t for kw in PORTFOLIO_KEYWORDS)
```

In the `/chat/stream` handler (find it — likely an async function that streams SSE deltas), add at the top BEFORE the existing `resolve_ticker` call:

```python
if is_portfolio_query(req.content):
    async def _portfolio_stream() -> AsyncIterator[bytes]:
        yield b"event: progress\ndata: " + json.dumps({"step": "analyzing_portfolio"}).encode() + b"\n\n"
        async for delta in run_lead_banker(
            user_message=req.content,
            resolution=None,
            client=get_client(),
            portfolio_mode=True,
            user_id=user["sub"],
        ):
            event_name = delta.pop("type", "delta")
            event = f"event: {event_name}\ndata: " + json.dumps(delta, default=str) + "\n\n"
            yield event.encode()
        yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(_portfolio_stream(), media_type="text/event-stream")
```

Adjust imports as needed (`json`, `AsyncIterator`, `StreamingResponse`, `get_client`, `run_lead_banker`). The handler likely already imports most.

If `_portfolio_stream` doesn't fit cleanly inline, factor it as a helper at module scope and call it from the handler.

- [ ] **Step 3: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_portfolio_mode.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Verify the existing ticker path didn't regress**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_chat_stream.py -v
```

Expected: all chat_stream tests still pass.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 154 passing (152 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/chat.py backend/tests/test_routes_chat_portfolio_mode.py && git commit -m "feat(backend): /chat/stream keyword detection routes portfolio queries

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Frontend "Analyze portfolio" button + chat prefill

**Files:**
- Modify: `frontend/components/portfolio-tab.tsx`
- Modify: `frontend/app/chat/page.tsx`

**Goal:** A button on `/portfolio` that navigates to `/chat?prefill=...` with a synthetic message. The chat page reads `prefill` from search params and either auto-fills the composer or auto-submits.

- [ ] **Step 1: Check current /chat page for prefill support**

Read `frontend/app/chat/page.tsx` — likely a composer + thread layout. Look for `useSearchParams`. If the page already supports `prefill`, just use it. Otherwise add the support.

- [ ] **Step 2: Add prefill support if missing**

In `frontend/app/chat/page.tsx`, near the top of the component:

```tsx
"use client";
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";

// ... inside the component:
const searchParams = useSearchParams();
const prefill = searchParams.get("prefill");

useEffect(() => {
  if (!prefill) return;
  // If the page exposes a state setter for the composer, set it.
  // If it auto-submits, call the submit handler.
  // Replace setComposerValue / handleSubmit with whatever the page uses.
  setComposerValue(prefill);
}, [prefill]);
```

If the chat page does not already have a `setComposerValue` or equivalent, add one. The exact wiring depends on the existing code — read it first, then minimal-diff edit.

If the chat page is a server component that doesn't easily host this state, factor a small client-component wrapper that holds the composer state.

- [ ] **Step 3: Add the Analyze button**

In `frontend/components/portfolio-tab.tsx`, locate the buttons row (where `+ Add position` lives). Add:

```tsx
import { useRouter } from "next/navigation";
// ...

const router = useRouter();
// ...

// Inside the buttons row, next to "+ Add position":
<button
  onClick={() => {
    if (!selectedId) return;
    const portfolio = portfolios.find((p) => p.id === selectedId);
    const name = portfolio?.name ?? "my portfolio";
    const prefill = `Analyze ${name}: cohort snapshot, returns vs benchmark, and risk metrics.`;
    router.push(`/chat?prefill=${encodeURIComponent(prefill)}`);
  }}
  disabled={!selectedId}
  className="border rounded-md px-3 py-1 text-sm disabled:opacity-50"
>
  Analyze portfolio
</button>
```

Place it BEFORE the `+ Add position` button so the workflow reads left-to-right: "Refresh prices | Analyze portfolio | + Add position".

- [ ] **Step 4: Build + lint**

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run build && npm run lint
```

Expected: both green.

- [ ] **Step 5: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/ && git commit -m "feat(frontend): Analyze portfolio button + chat prefill support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Playwright E2E for the analyze flow

**Files:**
- Modify: `frontend/e2e/portfolio.spec.ts`

**Goal:** One more browser test: click "Analyze portfolio" → expect a portfolio section to appear in the chat view within 30s. The test requires a live backend + frontend AND a real Anthropic API key (because the strategist is a Sonnet agent — there's no good way to mock it from the browser side).

- [ ] **Step 1: Add the test**

Append to `frontend/e2e/portfolio.spec.ts`:

```typescript
  test("analyze portfolio button shows a snapshot section in chat", async ({ page }) => {
    // Set up: need at least one position so the strategist has data
    await page.waitForLoadState("networkidle");
    const emptyPositions = page.getByText(/No positions yet/);
    if (await emptyPositions.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Need to create a portfolio + position first
      const newBtn = page.getByRole("button", { name: /^\+ New$/ }).first();
      if (await newBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await newBtn.click();
        await page.getByPlaceholder("Portfolio name").fill("Playwright Analyze");
        await page.getByRole("button", { name: "Create", exact: true }).click();
        await expect(emptyPositions).toBeHidden({ timeout: 5000 });
      }
      await page.getByRole("button", { name: "+ Add position" }).click();
      await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
      await page.getByRole("button", { name: "Resolve" }).click();
      await page.getByRole("button", { name: "Use this ticker" }).click();
      await page.getByLabel("Quantity").fill("10");
      await page.getByLabel(/Cost basis/).fill("150");
      await page.getByRole("button", { name: "Save position" }).click();
      await expect(page.getByText("AAPL")).toBeVisible({ timeout: 8000 });
    }

    // Click Analyze portfolio — navigates to /chat
    await page.getByRole("button", { name: "Analyze portfolio" }).click();
    await page.waitForURL("**/chat**", { timeout: 5000 });

    // Wait for the analysis to produce a section
    await expect(
      page.getByText(/Portfolio Snapshot|Returns vs Benchmark|Risk Metrics/),
    ).toBeVisible({ timeout: 60_000 });

    // Cleanup: nav back to portfolio and delete AAPL
    await page.goto("/portfolio");
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: /Delete/ }).first().click();
  });
```

- [ ] **Step 2: Run the existing tests + new one**

Start backend (in one terminal):

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1
```

Start frontend (in another):

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run dev
```

Run Playwright:

```bash
cd /Users/rakhisinha/Stylobate/frontend && \
  PLAYWRIGHT_TEST_EMAIL="rakhisinha100896@gmail.com" \
  PLAYWRIGHT_TEST_PASSWORD="Stylobate2026!" \
  npx playwright test --reporter=list
```

Expected: 4 passed (3 existing + 1 new).

- [ ] **Step 3: Stop servers**

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null
```

- [ ] **Step 4: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add frontend/e2e/portfolio.spec.ts && git commit -m "test(frontend): Playwright E2E for Analyze portfolio flow

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: End-to-end backend test + live UAT

**Files:**
- Create: `backend/tests/test_routes_e2e_portfolio_analysis.py`

**Goal:** One async test that exercises the full backend stack: insert positions in the live DB, call `/chat/stream` with a portfolio query, parse the SSE stream, assert sections appear. Then a manual UAT walkthrough.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_routes_e2e_portfolio_analysis.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.data.prices_cache import Price

TEST_USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


@pytest.mark.asyncio
async def test_chat_stream_portfolio_end_to_end(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """Portfolio query → Lead Banker dispatch → strategist → emit sections."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 50, "cost_basis": 150.0,
         "currency": "USD", "opened_at": date(2024, 6, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                              currency="USD", as_of=datetime(2026, 5, 15)),
    }

    # Mock the LLM responses for Lead Banker
    def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
        m = MagicMock()
        m.content = list(blocks)
        m.stop_reason = stop_reason
        m.usage = MagicMock(input_tokens=10, output_tokens=20,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return m

    def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
        return {"type": "tool_use", "id": tid, "name": name, "input": args}

    # Strategist sub-agent: get_holdings → submit
    strategist_responses = [
        _msg(_tu("get_holdings", {}, "g1")),
        _msg(_tu("submit_portfolio_findings", {
            "portfolio_id": PID,
            "portfolio_name": "Test",
            "cohorts": [{
                "cohort": ["USD", "equity_etf"], "positions_count": 1,
                "total_value_native": 10000.0, "total_cost_native": 7500.0,
                "gain_pct": 33.3, "weights": {"AAPL": 1.0},
                "returns_1mo": 5.0, "returns_3mo": 10.0, "returns_1y": 25.0,
                "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 15.0,
                "sharpe_1y": 1.1, "beta_1y": 1.0, "max_drawdown_1y": -0.10,
                "prices_partial": False,
            }],
            "rebalance": None, "notes": [], "citations": [],
            "confidence": 0.85,
        }, "s1"), stop_reason="tool_use"),
    ]
    # Lead Banker: dispatch_portfolio_strategist → emit_* → done
    lb_responses = [
        _msg(_tu("dispatch_portfolio_strategist",
                 {"brief": "Snapshot"}, "d1"), stop_reason="tool_use"),
        _msg(
            _tu("emit_quick_take", {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tu("emit_section", {"title": "Portfolio Snapshot",
                                  "markdown": "USD: $10k +33%", "citations": []}, "2"),
            _tu("emit_section", {"title": "Risks", "markdown": "concentrated.",
                                  "citations": []}, "3"),
            _tu("emit_disclaimer", {}, "4"),
            _tu("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ]
    all_responses = lb_responses + strategist_responses

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=all_responses)

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.portfolio._fetch_price_history_for_position",
               AsyncMock(return_value=[])), \
         patch("app.data.benchmarks.get_benchmark_history",
               AsyncMock(return_value=[])), \
         patch("app.routes.chat.get_client", return_value=fake_client), \
         patch("app.agents.portfolio_strategist.get_client", return_value=fake_client):
        r = await client.post(
            "/chat/stream",
            json={"content": "How is my portfolio doing?"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "event: done" in body
```

- [ ] **Step 2: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_e2e_portfolio_analysis.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 155 passing (154 prior + 1 new).

- [ ] **Step 4: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/tests/test_routes_e2e_portfolio_analysis.py && git commit -m "test(backend): end-to-end portfolio query through Lead Banker dispatch

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Push everything**

```bash
cd /Users/rakhisinha/Stylobate && git push origin main
```

Render will auto-deploy from `main`. Wait for it to go live (~2-3 min) before live UAT.

- [ ] **Step 6: Live UAT on local stack**

Start backend + frontend:

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1 &
cd /Users/rakhisinha/Stylobate/frontend && npm run dev &
```

Sign in at `http://localhost:3000/sign-in`. Navigate to `/portfolio`.

If you have positions left over from Phase 3A: skip to step 7. Otherwise add a USD position (AAPL, 10 sh, $150 cost), an INR position (RELIANCE.NS, 100, ₹1250 cost), and a crypto position (BTC, 0.5, $40000 cost).

- [ ] **Step 7: Click "Analyze portfolio"**

Expected behaviour:
- Navigates to `/chat` with the composer pre-filled.
- Within ~10s, see `progress: analyzing_portfolio`.
- Within ~30s, see at least: **Portfolio Snapshot** (3 cohort lines), **Returns vs Benchmark**, **Risk Metrics**.
- `event: done` at the end.

- [ ] **Step 8: Test rebalance via chat**

In the chat composer, type: `Rebalance my portfolio to 50% USD equities, 30% INR equities, 20% crypto.`

Expected:
- New **Rebalance Plan** section appears with cohort-level deltas in native currencies.
- Assumed USDINR rate disclosed in the section.

- [ ] **Step 9: Stop servers**

```bash
pkill -f "uvicorn app.main" 2>/dev/null
pkill -f "next dev" 2>/dev/null
```

- [ ] **Step 10: Deployed-stack smoke test**

Once Render reports the deploy live:

```bash
curl -s https://stylobate-backend.onrender.com/healthz
```

Then sign in at `https://stylobate.vercel.app/portfolio`, click Analyze, expect the same sections (modulo Yahoo rate-limit caveat from Phase 3A — if prices are partial, the analysis still runs on the subset).

## Report

Summarize:
- Section count and titles seen
- Per-cohort prices (sanity-check vs expected)
- Whether rebalance flow returned in-tolerance numbers
- Any rough edges to file as follow-ups

---

## End-of-Phase 3B acceptance checklist

- [ ] Backend tests: 155 passing (`uv run pytest -q`)
- [ ] ruff + mypy strict clean
- [ ] Playwright: 4 tests pass locally
- [ ] Live UAT on local stack: cohort snapshot + returns + risk + rebalance flow all visible
- [ ] Render + Vercel auto-deploys go green on `main`
- [ ] Deployed `https://stylobate.vercel.app/portfolio` → Analyze button works (modulo Yahoo rate-limit caveat)
- [ ] All 10 task commits on `main`

Phase 3C (Risk Manager: concentration, VaR, stress tests) is the next major plan.
