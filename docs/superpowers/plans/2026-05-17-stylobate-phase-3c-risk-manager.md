# Stylobate — Phase 3C: Risk Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Risk Manager Sonnet agent (concentration, VaR, correlations, stress tests) and rewire Lead Banker's portfolio_mode to dispatch it in parallel with the Portfolio Strategist from Phase 3B.

**Architecture:** Risk Manager mirrors 3B's pattern — pure-math module + closure-bound Tool factory + Sonnet agent. Lead Banker replaces `dispatch_portfolio_strategist_tool` with `dispatch_portfolio_analysts_tool` that runs both specialists via `asyncio.gather` and returns combined findings. The `/chat/portfolio` direct endpoint and its section renderer get new branches for the 4 risk sections.

**Tech Stack:** Existing — FastAPI, Pydantic v2, asyncpg, Anthropic Sonnet 4.6, yfinance, cache_kv. Reuses Phase 3B's `app/data/portfolio_stats.py`, `app/data/benchmarks.py`, `app/data/fx.py`, and `app/tools/portfolio.py` for shared DB reads.

**Spec reference:** `docs/superpowers/specs/2026-05-17-stylobate-phase-3c-risk-manager-design.md`. Every spec section maps to one or more tasks below.

---

## File map

```
backend/
  app/
    data/
      risk_stats.py                 NEW — pure math (T1)
    models/
      risk_manager.py               NEW — Pydantic models (T2)
    tools/
      risk.py                       NEW — 4 closure-bound Tool wrappers (T2)
    agents/
      risk_manager.py               NEW — Sonnet agent (T3)
      lead_banker.py                MODIFY — dispatch_portfolio_analysts; parallel gather (T4)
    routes/
      chat_portfolio.py             MODIFY — combined renderer with risk sections (T5)
    prompts/
      risk_manager.md               NEW — agent system prompt (T3)
      lead_banker.md                MODIFY — portfolio_mode synthesis spec (T4)
  tests/
    test_data_risk_stats.py                NEW (T1)
    test_tools_risk.py                     NEW (T2)
    test_risk_manager_agent.py             NEW (T3)
    test_lead_banker_portfolio_mode.py     MODIFY — new tool name + both specialists (T4)
    test_routes_chat_portfolio_endpoint.py MODIFY — combined findings + risk sections (T5)
    test_routes_e2e_portfolio_analysis.py  MODIFY — Lead Banker dispatch flow (T6)
```

---

## Glossary

- **Cohort key** = `(currency, asset_class_group)` with `asset_class_group ∈ {equity_etf, crypto}`. Imported from `app/data/portfolio_stats.py` (Phase 3B).
- **Closure-bound user_id:** Tools capture `user_id` (and optional `portfolio_id`) in their `impl` closure at agent-construction time. LLM cannot pass it. Same pattern as 3B `app/tools/portfolio.py`.
- **Parallel dispatch:** Lead Banker's new `dispatch_portfolio_analysts_tool` runs `run_portfolio_strategist` and `run_risk_manager` via `asyncio.gather(return_exceptions=True)`. Wall-clock latency is max(strategist, risk), not sum.
- **Pre-canned stress scenarios:** `rates_+200bps` (equity_etf -2%, crypto -5%), `equity_-20%` (equity_etf -20%, crypto -30%), `inr_depreciation_-10%` (INR cohort native unchanged; USD-equivalent total drops 10%).

---

## Pre-flight

- [ ] **Step P1: Confirm baseline tests pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -q
```

Expected: 155 passing (Phase 3B end state).

- [ ] **Step P2: Confirm lint + types clean**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests
```

Expected: `All checks passed!` + `Success: no issues found in 93 source files`.

- [ ] **Step P3: Confirm Phase 3B artifacts**

```bash
ls /Users/rakhisinha/Stylobate/backend/app/data/portfolio_stats.py \
   /Users/rakhisinha/Stylobate/backend/app/agents/portfolio_strategist.py \
   /Users/rakhisinha/Stylobate/backend/app/tools/portfolio.py 2>&1
```

Expected: all three files print.

---

## Task 1: Pure risk math — `risk_stats.py`

**Files:**
- Create: `backend/app/data/risk_stats.py`
- Create: `backend/tests/test_data_risk_stats.py`

**Goal:** No-I/O module: 4 public functions (`concentration_check`, `historical_var`, `pairwise_correlations`, `stress_test`) tested with synthetic fixtures.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_data_risk_stats.py`:

```python
from __future__ import annotations

from datetime import date, timedelta


def _days(n: int, start: float, step: float) -> list[tuple[date, float]]:
    d0 = date(2025, 5, 17)
    return [(d0 + timedelta(days=i), start + step * i) for i in range(n)]


def test_concentration_check_flags_position_over_10_pct() -> None:
    from app.data.risk_stats import concentration_check
    positions = [
        {"id": "p1", "ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 100, "current_price": 200.0},  # 20000 USD
        {"id": "p2", "ticker": "MSFT", "currency": "USD", "asset_class": "equity",
         "quantity": 5, "current_price": 400.0},    # 2000 USD
    ]
    # Total = 22000 USD. AAPL = 90.9% (critical), MSFT = 9.1% (no flag)
    flags = concentration_check(positions, usdinr=None)
    assert len(flags) == 1
    assert flags[0]["ticker"] == "AAPL"
    assert flags[0]["severity"] == "critical"
    assert flags[0]["weight_pct"] > 0.85


def test_concentration_check_warn_severity_for_10_to_20_pct() -> None:
    from app.data.risk_stats import concentration_check
    positions = [
        {"id": "p1", "ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "current_price": 150.0},   # 1500 USD = 15%
        {"id": "p2", "ticker": "MSFT", "currency": "USD", "asset_class": "equity",
         "quantity": 25, "current_price": 340.0},   # 8500 USD = 85%
    ]
    flags = concentration_check(positions, usdinr=None)
    by_ticker = {f["ticker"]: f for f in flags}
    assert by_ticker["MSFT"]["severity"] == "critical"
    assert by_ticker["AAPL"]["severity"] == "warn"
    assert 0.14 < by_ticker["AAPL"]["weight_pct"] < 0.16


def test_concentration_check_uses_usdinr_for_inr_positions() -> None:
    from app.data.risk_stats import concentration_check
    positions = [
        {"id": "p1", "ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 5, "current_price": 100.0},        # 500 USD
        {"id": "p2", "ticker": "RELIANCE.NS", "currency": "INR", "asset_class": "equity",
         "quantity": 100, "current_price": 4150.0},     # 415000 INR -> 5000 USD at 83
    ]
    # Total USD-eq = 5500. AAPL = 9.1% (no flag), RELIANCE = 90.9% (critical).
    flags = concentration_check(positions, usdinr=83.0)
    assert len(flags) == 1
    assert flags[0]["ticker"] == "RELIANCE.NS"
    assert flags[0]["severity"] == "critical"


def test_concentration_check_empty_when_no_positions() -> None:
    from app.data.risk_stats import concentration_check
    assert concentration_check([], usdinr=None) == []


def test_historical_var_returns_negative_pct_on_volatile_series() -> None:
    from app.data.risk_stats import historical_var
    # Build 252-day series where daily returns oscillate ±1% with occasional -3% drops
    series: list[tuple[date, float]] = []
    price = 100.0
    d0 = date(2025, 5, 17)
    for i in range(252):
        delta = -0.03 if i % 20 == 0 else (0.01 if i % 2 == 0 else -0.01)
        price = price * (1 + delta)
        series.append((d0 + timedelta(days=i), price))
    positions = [{"ticker": "X", "quantity": 1, "current_price": price}]
    out = historical_var({"X": series}, positions, confidence=0.95, horizon_days=10)
    assert out["insufficient_history"] is False
    assert out["var_pct"] is not None
    assert out["var_pct"] < 0
    assert out["var_native"] is not None
    assert out["var_native"] < 0


def test_historical_var_insufficient_history_returns_none() -> None:
    from app.data.risk_stats import historical_var
    short = _days(30, start=100.0, step=0.5)
    positions = [{"ticker": "X", "quantity": 1, "current_price": 115.0}]
    out = historical_var({"X": short}, positions, confidence=0.95, horizon_days=10)
    assert out["insufficient_history"] is True
    assert out["var_pct"] is None
    assert out["var_native"] is None


def test_pairwise_correlations_perfectly_correlated_series_return_one() -> None:
    from app.data.risk_stats import pairwise_correlations
    s = _days(200, start=100.0, step=0.5)
    positions = [
        {"ticker": "A", "quantity": 1, "current_price": 200.0},
        {"ticker": "B", "quantity": 1, "current_price": 200.0},
    ]
    history = {"A": s, "B": list(s)}  # identical series
    out = pairwise_correlations(positions, history, min_overlap=100)
    assert out["tickers"] == ["A", "B"]
    assert abs(out["matrix"][0][1] - 1.0) < 1e-6
    assert abs(out["matrix"][1][0] - 1.0) < 1e-6
    # Diagonal is 1.0
    assert out["matrix"][0][0] == 1.0
    assert out["excluded"] == []


def test_pairwise_correlations_excludes_short_history() -> None:
    from app.data.risk_stats import pairwise_correlations
    long_series = _days(200, start=100.0, step=0.5)
    short_series = _days(30, start=100.0, step=0.5)
    positions = [
        {"ticker": "LONG", "quantity": 1, "current_price": 200.0},
        {"ticker": "SHORT", "quantity": 1, "current_price": 115.0},
    ]
    history = {"LONG": long_series, "SHORT": short_series}
    out = pairwise_correlations(positions, history, min_overlap=100)
    assert "SHORT" in out["excluded"]
    assert out["tickers"] == ["LONG"]


def test_stress_test_rates_scenario_shocks_equity_and_crypto() -> None:
    from app.data.risk_stats import stress_test
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "current_price": 100.0},
        {"ticker": "BTC", "currency": "USD", "asset_class": "crypto",
         "quantity": 1, "current_price": 50000.0},
    ]
    out = stress_test(positions, scenario="rates_+200bps", usdinr=None)
    # AAPL: 1000 USD * -2% = -20
    # BTC: 50000 USD * -5% = -2500
    per_ticker = {p["ticker"]: p for p in out["per_position"]}
    assert abs(per_ticker["AAPL"]["delta_native"] - (-20.0)) < 1e-6
    assert abs(per_ticker["BTC"]["delta_native"] - (-2500.0)) < 1e-6
    assert abs(out["total_delta_usd"] - (-2520.0)) < 1e-6


def test_stress_test_equity_minus_20_pct() -> None:
    from app.data.risk_stats import stress_test
    positions = [
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "current_price": 100.0},
    ]
    out = stress_test(positions, scenario="equity_-20%", usdinr=None)
    assert abs(out["per_position"][0]["delta_native"] - (-200.0)) < 1e-6


def test_stress_test_inr_depreciation_drops_usd_equivalent_only() -> None:
    from app.data.risk_stats import stress_test
    positions = [
        {"ticker": "RELIANCE.NS", "currency": "INR", "asset_class": "equity",
         "quantity": 100, "current_price": 1000.0},  # 100000 INR
        {"ticker": "AAPL", "currency": "USD", "asset_class": "equity",
         "quantity": 10, "current_price": 100.0},    # 1000 USD
    ]
    out = stress_test(positions, scenario="inr_depreciation_-10%", usdinr=83.0)
    per_ticker = {p["ticker"]: p for p in out["per_position"]}
    # INR position native unchanged
    assert per_ticker["RELIANCE.NS"]["delta_native"] == 0
    # USD position unaffected
    assert per_ticker["AAPL"]["delta_native"] == 0
    # USD-eq total change: INR cohort was 100000 INR / 83 = ~1204.8 USD,
    # after 10% INR depreciation that's 100000 / (83/0.9) = ~1084.3 USD.
    # Delta ≈ -120.5 USD
    assert -150.0 < out["total_delta_usd"] < -100.0
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_risk_stats.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.data.risk_stats'`.

- [ ] **Step 3: Implement `risk_stats.py`**

Create `backend/app/data/risk_stats.py`:

```python
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from statistics import mean, pstdev
from typing import Any, Literal

CohortGroup = Literal["equity_etf", "crypto"]
CohortKey = tuple[str, CohortGroup]

_MIN_DAYS_FOR_VAR = 100
_TRADING_DAYS = 252


def _cohort_key(position: dict[str, Any]) -> CohortKey:
    group: CohortGroup = "crypto" if position["asset_class"] == "crypto" else "equity_etf"
    return (position["currency"], group)


def _position_value_native(p: dict[str, Any]) -> float:
    qty = float(p["quantity"])
    price = float(p.get("current_price") or 0)
    return qty * price


def _position_value_usd(p: dict[str, Any], usdinr: float | None) -> float | None:
    """Convert position value to USD-equivalent. Returns None if INR and FX unavailable."""
    native = _position_value_native(p)
    if p["currency"] == "USD":
        return native
    if p["currency"] == "INR" and usdinr:
        return native / usdinr
    return None  # Other currencies — not in scope for 3C


# ---------------------------------------------------------------------------
# 1. concentration_check
# ---------------------------------------------------------------------------
def concentration_check(
    positions: list[dict[str, Any]], usdinr: float | None,
) -> list[dict[str, Any]]:
    """Flag positions whose USD-equivalent weight > 10%. Empty list if no flags."""
    if not positions:
        return []
    # Compute USD-eq for each priced position
    valued: list[tuple[dict[str, Any], float]] = []
    for p in positions:
        v = _position_value_usd(p, usdinr)
        if v is not None and v > 0:
            valued.append((p, v))
    if not valued:
        return []
    total_usd = sum(v for _, v in valued)
    if total_usd <= 0:
        return []
    flags: list[dict[str, Any]] = []
    for p, v in valued:
        weight = v / total_usd
        if weight > 0.10:
            severity = "critical" if weight > 0.20 else "warn"
            flags.append({
                "position_id": p.get("id"),
                "ticker": p["ticker"],
                "weight_pct": weight,
                "threshold_pct": 0.10,
                "severity": severity,
            })
    flags.sort(key=lambda f: -f["weight_pct"])
    return flags


# ---------------------------------------------------------------------------
# 2. historical_var (per single cohort)
# ---------------------------------------------------------------------------
def _portfolio_value_series(
    positions: list[dict[str, Any]],
    price_history: dict[str, list[tuple[date, float]]],
) -> list[tuple[date, float]]:
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
    qty_by_ticker = {p["ticker"]: float(p["quantity"]) for p in positions}
    return [
        (
            d,
            sum(qty_by_ticker[t] * day_map[d] for t, day_map in by_ticker.items()),
        )
        for d in sorted(common_dates)
    ]


def _daily_log_returns(series: list[tuple[date, float]]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        cur = series[i][1]
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def historical_var(
    price_history: dict[str, list[tuple[date, float]]],
    positions: list[dict[str, Any]],
    confidence: float = 0.95,
    horizon_days: int = 10,
) -> dict[str, Any]:
    """Historical VaR for the given set of positions (one cohort).

    Returns {var_pct, var_native, insufficient_history}.
    var_pct < 0 represents the (1-confidence)% percentile horizon-day loss.
    var_native is var_pct * current_cohort_value.
    """
    port_series = _portfolio_value_series(positions, price_history)
    daily = _daily_log_returns(port_series)
    if len(daily) < _MIN_DAYS_FOR_VAR:
        return {"var_pct": None, "var_native": None, "insufficient_history": True}
    # Rolling horizon_days cumulative log-return
    h = horizon_days
    if h <= 0 or h > len(daily):
        return {"var_pct": None, "var_native": None, "insufficient_history": True}
    rolling: list[float] = [sum(daily[i - h + 1 : i + 1]) for i in range(h - 1, len(daily))]
    rolling.sort()
    pct_index = int((1 - confidence) * len(rolling))
    if pct_index >= len(rolling):
        pct_index = len(rolling) - 1
    var_log = rolling[pct_index]
    var_pct = math.exp(var_log) - 1.0  # negative
    current_value = sum(_position_value_native(p) for p in positions)
    var_native = var_pct * current_value
    return {
        "var_pct": var_pct,
        "var_native": var_native,
        "insufficient_history": False,
    }


# ---------------------------------------------------------------------------
# 3. pairwise_correlations
# ---------------------------------------------------------------------------
def _aligned_returns(
    a: list[tuple[date, float]], b: list[tuple[date, float]],
) -> tuple[list[float], list[float]]:
    """Inner-join two date series; return daily log-returns of each on the joined dates."""
    bd = {d: v for d, v in b}
    a_close: list[tuple[date, float]] = []
    b_close: list[tuple[date, float]] = []
    for d, va in a:
        vb = bd.get(d)
        if vb is not None:
            a_close.append((d, va))
            b_close.append((d, vb))
    return _daily_log_returns(a_close), _daily_log_returns(b_close)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = mean(xs)
    my = mean(ys)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs))) / len(xs)
    sx = pstdev(xs)
    sy = pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def pairwise_correlations(
    positions: list[dict[str, Any]],
    price_history: dict[str, list[tuple[date, float]]],
    min_overlap: int = _MIN_DAYS_FOR_VAR,
) -> dict[str, Any]:
    """Pairwise Pearson correlation on daily log-returns.

    Positions with insufficient overlap with EVERY other position are excluded.
    Returns {tickers, matrix (NxN), excluded}.
    """
    tickers = [p["ticker"] for p in positions]
    included: list[str] = []
    excluded: list[str] = []
    aligned_returns: dict[str, dict[str, list[float]]] = defaultdict(dict)
    # Precompute aligned returns for every pair
    for i, ti in enumerate(tickers):
        hi = price_history.get(ti)
        if not hi:
            excluded.append(ti)
            continue
        survives = False
        for j, tj in enumerate(tickers):
            if i == j:
                continue
            hj = price_history.get(tj)
            if not hj:
                continue
            ri, rj = _aligned_returns(hi, hj)
            if len(ri) >= min_overlap:
                aligned_returns[ti][tj] = ri
                aligned_returns[tj][ti] = rj
                survives = True
        if survives:
            included.append(ti)
        else:
            excluded.append(ti)
    matrix: list[list[float]] = []
    for ti in included:
        row: list[float] = []
        for tj in included:
            if ti == tj:
                row.append(1.0)
                continue
            ri = aligned_returns.get(ti, {}).get(tj)
            rj = aligned_returns.get(tj, {}).get(ti)
            if ri is None or rj is None:
                row.append(0.0)
                continue
            r = _pearson(ri, rj)
            row.append(r if r is not None else 0.0)
        matrix.append(row)
    return {"tickers": included, "matrix": matrix, "excluded": excluded}


# ---------------------------------------------------------------------------
# 4. stress_test
# ---------------------------------------------------------------------------
SHOCK_BY_SCENARIO: dict[str, dict[CohortGroup, float]] = {
    "rates_+200bps": {"equity_etf": -0.02, "crypto": -0.05},
    "equity_-20%": {"equity_etf": -0.20, "crypto": -0.30},
    "inr_depreciation_-10%": {},  # special-cased
}


def stress_test(
    positions: list[dict[str, Any]], scenario: str, usdinr: float | None,
) -> dict[str, Any]:
    """Apply a pre-canned shock. Returns per-position + by-cohort + total_delta_usd."""
    if scenario not in SHOCK_BY_SCENARIO:
        raise ValueError(f"unknown scenario: {scenario}")

    per_position: list[dict[str, Any]] = []
    cohort_before: dict[CohortKey, float] = defaultdict(float)
    cohort_after: dict[CohortKey, float] = defaultdict(float)
    total_delta_usd = 0.0
    assumed_fx: dict[str, float] = {}

    if scenario == "inr_depreciation_-10%":
        for p in positions:
            before = _position_value_native(p)
            after = before  # native unchanged
            delta_native = 0.0
            key = _cohort_key(p)
            cohort_before[key] += before
            cohort_after[key] += after
            per_position.append({
                "ticker": p["ticker"],
                "before_native": before,
                "after_native": after,
                "delta_native": delta_native,
                "delta_pct": 0.0,
            })
        # USD-equivalent total: INR cohorts effectively lose 10% of their USD-eq value
        if usdinr:
            assumed_fx["USDINR"] = usdinr
            depreciated_usdinr = usdinr / 0.90
            for key, before_native in cohort_before.items():
                currency = key[0]
                if currency == "INR":
                    before_usd = before_native / usdinr
                    after_usd = before_native / depreciated_usdinr
                    total_delta_usd += after_usd - before_usd
                # USD cohorts: no FX impact on USD-eq
    else:
        shocks = SHOCK_BY_SCENARIO[scenario]
        for p in positions:
            key = _cohort_key(p)
            shock = shocks.get(key[1], 0.0)
            before = _position_value_native(p)
            after = before * (1 + shock)
            delta_native = after - before
            cohort_before[key] += before
            cohort_after[key] += after
            per_position.append({
                "ticker": p["ticker"],
                "before_native": before,
                "after_native": after,
                "delta_native": delta_native,
                "delta_pct": shock * 100.0,
            })
            # USD-equivalent
            if p["currency"] == "USD":
                total_delta_usd += delta_native
            elif p["currency"] == "INR" and usdinr:
                total_delta_usd += delta_native / usdinr
                assumed_fx["USDINR"] = usdinr

    by_cohort = [
        {
            "cohort": [k[0], k[1]],
            "before_native": cohort_before[k],
            "after_native": cohort_after[k],
            "delta_native": cohort_after[k] - cohort_before[k],
        }
        for k in cohort_before
    ]

    return {
        "scenario": scenario,
        "per_position": per_position,
        "by_cohort": by_cohort,
        "total_delta_usd": total_delta_usd,
        "assumed_fx": assumed_fx,
    }
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_data_risk_stats.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 166 passing (155 prior + 11 new). ruff + mypy strict clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/data/risk_stats.py backend/tests/test_data_risk_stats.py && git commit -m "feat(backend): risk_stats pure math — concentration, VaR, correlations, stress tests

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Pydantic models + risk tools (closure-bound)

**Files:**
- Create: `backend/app/models/risk_manager.py`
- Create: `backend/app/tools/risk.py`
- Create: `backend/tests/test_tools_risk.py`

**Goal:** 4 closure-bound `Tool` wrappers backing the 4 risk_stats functions. user_id NEVER in input_schema; tools accept `**_kwargs` to absorb attacker-supplied keys.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tools_risk.py`:

```python
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.data.prices_cache import Price
from app.data.yfinance_adapter import Bar

USER_ID = "00000000-0000-0000-0000-000000000099"
PID = "00000000-0000-0000-0000-000000000001"


def _async_cm(value: Any) -> Any:
    class _CM:
        async def __aenter__(self) -> Any: return value
        async def __aexit__(self, *_a: Any) -> None: pass
    return _CM()


def _bars(n: int) -> list[Bar]:
    return [
        Bar(date=date(2025, 5, 17), open=100.0, high=100.0, low=100.0, close=100.0 + i, volume=0)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_concentration_check_uses_closure_user_id() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 100, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "MSFT", "market": "US",
         "asset_class": "equity", "quantity": 5, "cost_basis": 400.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
        ("MSFT", "US"): Price(ticker="MSFT", market="US", price=400.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    conc = next(t for t in tools if t.name == "concentration_check")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        # LLM tries to override user_id
        result = await conc.impl(user_id="ATTACKER")

    # Closure user_id was used, not "ATTACKER"
    actual_args = conn.fetch.await_args.args
    assert UUID(USER_ID) in actual_args
    assert "ATTACKER" not in [str(a) for a in actual_args]
    # AAPL is 100*200=20000, MSFT is 5*400=2000, total 22000. AAPL = 90.9%.
    flags = result["flags"]
    assert len(flags) == 1
    assert flags[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_calc_var_aggregates_per_cohort() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "BTC", "market": "CRYPTO",
         "asset_class": "crypto", "quantity": 1, "cost_basis": 30000.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=150.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
        ("BTC", "CRYPTO"): Price(ticker="BTC", market="CRYPTO", price=80000.0,
                                 currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    calc = next(t for t in tools if t.name == "calc_var")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk._fetch_price_history",
               AsyncMock(return_value=_bars(20))):  # insufficient history
        result = await calc.impl()

    cohorts = {tuple(c["cohort"]): c for c in result["var_by_cohort"]}
    assert ("USD", "equity_etf") in cohorts
    assert ("USD", "crypto") in cohorts
    # All insufficient_history=True because we returned only 20 bars
    assert all(c["insufficient_history"] for c in cohorts.values())


@pytest.mark.asyncio
async def test_get_correlations_returns_matrix() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "A", "market": "US",
         "asset_class": "equity", "quantity": 1, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
        {"id": UUID("00000000-0000-0000-0000-0000000000a2"),
         "portfolio_id": UUID(PID), "ticker": "B", "market": "US",
         "asset_class": "equity", "quantity": 1, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("A", "US"): Price(ticker="A", market="US", price=200.0,
                           currency="USD", as_of=datetime(2026, 5, 17)),
        ("B", "US"): Price(ticker="B", market="US", price=200.0,
                           currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    corr = next(t for t in tools if t.name == "get_correlations")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk._fetch_price_history",
               AsyncMock(return_value=_bars(200))):
        result = await corr.impl()

    assert "A" in result["tickers"]
    assert "B" in result["tickers"]
    assert len(result["matrix"]) == 2
    assert result["matrix"][0][0] == 1.0  # diagonal


@pytest.mark.asyncio
async def test_stress_test_rejects_unknown_scenario() -> None:
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    stress = next(t for t in tools if t.name == "stress_test")
    result = await stress.impl(scenario="unknown_scenario")
    assert "error" in result


@pytest.mark.asyncio
async def test_stress_test_equity_minus_20_pct_on_aapl() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 10, "cost_basis": 100.0,
         "currency": "USD", "opened_at": date(2024, 1, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})
    fake_prices = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=100.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=PID)
    stress = next(t for t in tools if t.name == "stress_test")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value=fake_prices)), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        result = await stress.impl(scenario="equity_-20%")

    assert result["scenario"] == "equity_-20%"
    # AAPL 10*100=1000, -20% = -200
    assert abs(result["total_delta_usd"] - (-200.0)) < 1e-6


@pytest.mark.asyncio
async def test_concentration_check_no_portfolios_returns_empty_flags() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)

    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id=USER_ID, portfolio_id=None)
    conc = next(t for t in tools if t.name == "concentration_check")

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices", AsyncMock(return_value={})), \
         patch("app.tools.risk.get_usdinr", AsyncMock(return_value=None)):
        result = await conc.impl()

    assert result["flags"] == []
    assert result["portfolio_id"] is None
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_tools_risk.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the models**

Create `backend/app/models/risk_manager.py`:

```python
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

CohortGroup = Literal["equity_etf", "crypto"]


class ConcentrationFlag(BaseModel):
    position_id: UUID | None = None
    ticker: str
    weight_pct: float
    threshold_pct: float = 0.10
    severity: Literal["warn", "critical"]


class CohortVaR(BaseModel):
    cohort: tuple[str, CohortGroup]
    confidence: float
    horizon_days: int
    var_pct: float | None = None
    var_native: float | None = None
    methodology: Literal["historical"] = "historical"
    insufficient_history: bool = False


class CorrelationMatrix(BaseModel):
    tickers: list[str]
    matrix: list[list[float]]
    excluded: list[str] = []


class PositionStressDelta(BaseModel):
    ticker: str
    before_native: float
    after_native: float
    delta_native: float
    delta_pct: float


class CohortStressDelta(BaseModel):
    cohort: tuple[str, CohortGroup]
    before_native: float
    after_native: float
    delta_native: float


class StressResult(BaseModel):
    scenario: Literal["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"]
    per_position: list[PositionStressDelta]
    by_cohort: list[CohortStressDelta]
    total_delta_usd: float
    assumed_fx: dict[str, float] = {}


class Citation(BaseModel):
    source: str
    ref: str
```

- [ ] **Step 4: Implement the tools**

Create `backend/app/tools/risk.py`:

```python
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls
from typing import Any

from app.data.fx import get_usdinr
from app.data.risk_stats import (
    concentration_check,
    historical_var,
    pairwise_correlations,
    stress_test,
)
from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history
from app.tools.base import Tool

logger = logging.getLogger(__name__)


async def _shared_load(
    user_id: str, portfolio_id: str | None,
) -> tuple[str | None, list[dict[str, Any]], float | None]:
    """Return (portfolio_id, positions-with-current-price, usdinr)."""
    from app.tools.portfolio import _holdings_with_prices
    pid, positions = await _holdings_with_prices(user_id, portfolio_id)
    usdinr = await get_usdinr() if positions else None
    return pid, positions, usdinr


async def _fetch_history_for_positions(
    positions: list[dict[str, Any]],
) -> dict[str, list[tuple[date_cls, float]]]:
    """Fetch 1y daily history for each ticker. Failures map to empty lists."""
    out: dict[str, list[tuple[date_cls, float]]] = {}
    for p in positions:
        try:
            bars = await _fetch_price_history(
                p["ticker"], period="1y", interval="1d", market=p["market"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("history fetch failed for %s: %s", p["ticker"], e)
            bars = []
        out[p["ticker"]] = [(b.date, float(b.close)) for b in bars]
    return out


def _cohort_key(p: dict[str, Any]) -> tuple[str, str]:
    group = "crypto" if p["asset_class"] == "crypto" else "equity_etf"
    return (p["currency"], group)


def build_risk_tools(*, user_id: str, portfolio_id: str | None = None) -> list[Tool]:
    """Build 4 risk tools whose impls capture user_id + portfolio_id."""

    async def _concentration_check(**_kwargs: Any) -> dict[str, Any]:
        pid, positions, usdinr = await _shared_load(user_id, portfolio_id)
        flags = concentration_check(positions, usdinr)
        notes: list[str] = []
        if positions and usdinr is None and any(p["currency"] == "INR" for p in positions):
            notes.append(
                "USDINR unavailable — concentration computed without cross-currency comparison."
            )
        return {
            "portfolio_id": pid,
            "flags": flags,
            "fx_used": usdinr,
            "notes": notes,
        }

    async def _calc_var(
        confidence: float = 0.95, horizon_days: int = 10, **_kwargs: Any,
    ) -> dict[str, Any]:
        pid, positions, _ = await _shared_load(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "var_by_cohort": []}
        price_history = await _fetch_history_for_positions(positions)
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for p in positions:
            buckets[_cohort_key(p)].append(p)
        out: list[dict[str, Any]] = []
        for cohort, ps in buckets.items():
            result = historical_var(
                price_history, ps,
                confidence=confidence, horizon_days=horizon_days,
            )
            out.append({
                "cohort": [cohort[0], cohort[1]],
                "confidence": confidence,
                "horizon_days": horizon_days,
                "methodology": "historical",
                **result,
            })
        return {"portfolio_id": pid, "var_by_cohort": out}

    async def _get_correlations(**_kwargs: Any) -> dict[str, Any]:
        pid, positions, _ = await _shared_load(user_id, portfolio_id)
        if not positions or len(positions) < 2:
            return {"portfolio_id": pid, "tickers": [], "matrix": [], "excluded": []}
        price_history = await _fetch_history_for_positions(positions)
        result = pairwise_correlations(positions, price_history)
        return {"portfolio_id": pid, **result}

    async def _stress_test(scenario: str = "", **_kwargs: Any) -> dict[str, Any]:
        valid = {"rates_+200bps", "equity_-20%", "inr_depreciation_-10%"}
        if scenario not in valid:
            return {
                "error": f"unknown scenario: {scenario!r}",
                "valid_scenarios": sorted(valid),
            }
        pid, positions, usdinr = await _shared_load(user_id, portfolio_id)
        if not positions:
            return {"portfolio_id": pid, "scenario": scenario,
                    "per_position": [], "by_cohort": [], "total_delta_usd": 0.0,
                    "assumed_fx": {}}
        result = stress_test(positions, scenario=scenario, usdinr=usdinr)
        return {"portfolio_id": pid, **result}

    return [
        Tool(
            name="concentration_check",
            description=(
                "Flag positions whose USD-equivalent weight exceeds 10% of the portfolio. "
                "Severity: 'warn' (10-20%) or 'critical' (>20%). Falls back to per-cohort "
                "weights if USDINR is unavailable."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_concentration_check,
        ),
        Tool(
            name="calc_var",
            description=(
                "Compute historical Value-at-Risk per cohort. var_pct is negative; "
                "var_native is the absolute amount in cohort's currency. Requires >=100 days "
                "of price history per position; insufficient cohorts return null."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "confidence": {"type": "number", "default": 0.95},
                    "horizon_days": {"type": "integer", "default": 10},
                },
                "required": [],
            },
            impl=_calc_var,
        ),
        Tool(
            name="get_correlations",
            description=(
                "Pairwise Pearson correlation matrix on daily log-returns over 1y. "
                "Positions with <100 days of overlap are excluded."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            impl=_get_correlations,
        ),
        Tool(
            name="stress_test",
            description=(
                "Apply a pre-canned stress scenario. Returns per-position deltas in native "
                "currency, by-cohort totals, and a USD-equivalent total via USDINR. "
                "Scenarios: 'rates_+200bps' (equity -2%, crypto -5%), 'equity_-20%' "
                "(equity -20%, crypto -30%), 'inr_depreciation_-10%' (INR cohort loses 10% "
                "USD-equivalent; native value unchanged)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"],
                    },
                },
                "required": ["scenario"],
            },
            impl=_stress_test,
        ),
    ]
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_tools_risk.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 172 passing (166 prior + 6 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/models/risk_manager.py backend/app/tools/risk.py backend/tests/test_tools_risk.py && git commit -m "feat(backend): risk tools (closure-bound user_id, 4 tools)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Risk Manager Sonnet agent

**Files:**
- Create: `backend/app/agents/risk_manager.py`
- Create: `backend/app/prompts/risk_manager.md`
- Create: `backend/tests/test_risk_manager_agent.py`

**Goal:** Sonnet agent with 8-turn loop. Mirrors `app/agents/portfolio_strategist.py` (Phase 3B) exactly — same submit-marker pattern, same closure-passed tools.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_risk_manager_agent.py`:

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
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


@pytest.mark.asyncio
async def test_risk_manager_runs_all_tools_then_submits() -> None:
    """The agent calls concentration_check → calc_var → get_correlations → stress_test×3
    → submit_risk_findings exactly once."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id="p-1")
    for t in tools:
        if t.name == "concentration_check":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "flags": []})
        elif t.name == "calc_var":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "var_by_cohort": []})
        elif t.name == "get_correlations":
            t.impl = AsyncMock(return_value={"portfolio_id": "p-1", "tickers": [],
                                              "matrix": [], "excluded": []})
        elif t.name == "stress_test":
            t.impl = AsyncMock(return_value={"scenario": "x", "per_position": [],
                                              "by_cohort": [], "total_delta_usd": 0.0,
                                              "assumed_fx": {}})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("concentration_check", {}, "c1")),
        _message(_tool_use("calc_var", {}, "v1")),
        _message(_tool_use("get_correlations", {}, "x1")),
        _message(_tool_use("stress_test", {"scenario": "rates_+200bps"}, "s1")),
        _message(_tool_use("stress_test", {"scenario": "equity_-20%"}, "s2")),
        _message(_tool_use("stress_test", {"scenario": "inr_depreciation_-10%"}, "s3")),
        _message(
            _tool_use("submit_risk_findings", {
                "portfolio_id": "p-1",
                "portfolio_name": "Test",
                "concentration": [],
                "var_by_cohort": [],
                "correlations": None,
                "stress_results": [],
                "notes": [],
                "citations": [],
                "confidence": 0.85,
            }, "f1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id="p-1", brief="full risk analysis",
        client=fake_client, tools_override=tools,
    )
    assert findings["portfolio_id"] == "p-1"
    assert findings["confidence"] == 0.85
    # Verify all tools were exercised
    conc = next(t for t in tools if t.name == "concentration_check")
    stress = next(t for t in tools if t.name == "stress_test")
    assert conc.impl.await_count == 1
    assert stress.impl.await_count == 3


@pytest.mark.asyncio
async def test_risk_manager_short_circuits_on_no_portfolios() -> None:
    """concentration_check returns empty flags + portfolio_id=None → agent surfaces empty findings."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id=None)
    for t in tools:
        if t.name == "concentration_check":
            t.impl = AsyncMock(return_value={"portfolio_id": None, "flags": []})
        else:
            t.impl = AsyncMock(return_value={})

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("concentration_check", {}, "c1")),
        _message(
            _tool_use("submit_risk_findings", {
                "portfolio_id": None,
                "portfolio_name": "",
                "concentration": [],
                "var_by_cohort": [],
                "correlations": None,
                "stress_results": [],
                "notes": ["No portfolio to analyze. Visit /portfolio to create one."],
                "citations": [],
                "confidence": 1.0,
            }, "f1"),
            stop_reason="tool_use",
        ),
    ])

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id=None, brief="risk",
        client=fake_client, tools_override=tools,
    )
    assert findings["portfolio_id"] is None
    assert "create one" in findings["notes"][0]


@pytest.mark.asyncio
async def test_risk_manager_falls_back_when_loop_exhausts() -> None:
    """If the LLM never submits within turn budget, agent returns a default empty findings."""
    from app.tools.risk import build_risk_tools
    tools = build_risk_tools(user_id="u-1", portfolio_id="p-1")
    for t in tools:
        t.impl = AsyncMock(return_value={})

    # Mock keeps emitting useless text blocks; no submit ever
    def _text(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_text("thinking..."), stop_reason="end_turn"),
    ] * 10)

    from app.agents.risk_manager import run_risk_manager
    findings = await run_risk_manager(
        user_id="u-1", portfolio_id="p-1", brief="risk",
        client=fake_client, tools_override=tools,
    )
    assert findings["confidence"] == 0.0
    assert "turn budget" in findings["notes"][0]
```

- [ ] **Step 2: Confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_risk_manager_agent.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the prompt**

Create `backend/app/prompts/risk_manager.md`:

```markdown
You are the Risk Manager inside Stylobate. Your job: surface concentration risks, quantify potential downside (VaR + stress tests), and report cross-position correlations. You do NOT recommend trades — that's the Portfolio Strategist's domain. You report risks; the Lead Banker decides whether to surface action items.

You have 4 tools:

- `concentration_check()` — flag positions >10% of portfolio
- `calc_var(confidence?, horizon_days?)` — historical VaR per cohort
- `get_correlations()` — pairwise correlation matrix
- `stress_test(scenario)` — one of "rates_+200bps", "equity_-20%", "inr_depreciation_-10%"

Process:
1. Call `concentration_check` first (cheapest; informs everything else).
2. If concentration_check returns `portfolio_id=null`, call `submit_risk_findings` immediately with `notes=["No portfolio to analyze. Visit /portfolio to create one."]` and stop.
3. Otherwise call `calc_var()` with defaults (95% confidence, 10-day horizon).
4. Call `get_correlations()` once.
5. Call `stress_test(scenario=X)` THREE times — once per pre-canned scenario.
6. Call `submit_risk_findings` exactly once.

Discipline:
- Concentration severity: 10-20% = "warn", >20% = "critical".
- VaR is a probabilistic loss estimate, not a worst case. State the methodology in `notes`.
- Correlation matrix is currency-agnostic (returns are unitless).
- Stress test scenarios are SIMPLIFIED: actual sensitivity varies by sector, not just asset class. State this caveat in `notes` when surfacing stress results.
- `notes` calls out: missing FX (concentration falls back to per-cohort), insufficient history (positions excluded from VaR/correlations), partial price data, scenario simplifications.
- `citations` reference "yfinance" for prices.
- `confidence`: 0.85+ when concentration + ≥3 stress results computed; 0.7 if some cohorts had insufficient history; <0.6 if many gaps.
```

- [ ] **Step 4: Implement the agent**

Create `backend/app/agents/risk_manager.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolUseBlock

from app.core.anthropic_client import get_client
from app.tools.base import Tool
from app.tools.risk import build_risk_tools

_SUBMIT_TOOL = Tool(
    name="submit_risk_findings",
    description="Submit the final risk analysis. Call exactly once at the end.",
    input_schema={
        "type": "object",
        "properties": {
            "portfolio_id": {"type": ["string", "null"]},
            "portfolio_name": {"type": "string"},
            "concentration": {"type": "array"},
            "var_by_cohort": {"type": "array"},
            "correlations": {"type": ["object", "null"]},
            "stress_results": {"type": "array"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": [
            "portfolio_id", "portfolio_name", "concentration",
            "var_by_cohort", "stress_results", "notes", "confidence",
        ],
    },
    impl=None,  # type: ignore[arg-type]  # marker tool; handled inline
)


def _load_system_prompt() -> str:
    path = Path(__file__).parent.parent / "prompts" / "risk_manager.md"
    return path.read_text(encoding="utf-8")


async def run_risk_manager(
    *,
    user_id: str,
    portfolio_id: str | None,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """Run the Risk Manager Sonnet agent. Returns a risk findings dict.

    user_id is closure-scoped into the tools — never passed to the LLM.
    """
    c = client or get_client()
    tools = tools_override or build_risk_tools(user_id=user_id, portfolio_id=portfolio_id)
    all_tools = [*tools, _SUBMIT_TOOL]
    tool_schemas = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in all_tools
    ]
    tools_by_name = {t.name: t for t in tools}

    user_content = (
        f"Brief: {brief}\n"
        f"Portfolio ID: {portfolio_id or '(default: first)'}\n"
        "\nUse the tools per the prompt's process: concentration_check first, "
        "then calc_var, get_correlations, stress_test×3, then submit_risk_findings."
    )
    messages: list[MessageParam] = [{"role": "user", "content": user_content}]

    findings: dict[str, Any] | None = None
    for _turn in range(8):
        resp = await c.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_load_system_prompt(),
            tools=tool_schemas,
            messages=messages,
        )
        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if isinstance(block, dict):
                btype = block.get("type")
                name = block.get("name", "")
                block_id = cast(str, block.get("id", ""))
                args = cast(dict[str, Any], block.get("input", {})) or {}
            else:
                btype = getattr(block, "type", None)
                if btype != "tool_use":
                    continue
                tu = cast(ToolUseBlock, block)
                name = tu.name
                block_id = tu.id
                args = cast(dict[str, Any], tu.input) or {}
            if btype != "tool_use":
                continue
            if name == "submit_risk_findings":
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
            "portfolio_id": portfolio_id,
            "portfolio_name": "",
            "concentration": [],
            "var_by_cohort": [],
            "correlations": None,
            "stress_results": [],
            "notes": ["Agent did not submit findings within turn budget."],
            "citations": [],
            "confidence": 0.0,
        }
    return findings
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_risk_manager_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 175 passing (172 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/agents/risk_manager.py backend/app/prompts/risk_manager.md backend/tests/test_risk_manager_agent.py && git commit -m "feat(backend): Risk Manager Sonnet agent (8-turn loop, closure-bound user_id)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Lead Banker parallel dispatch + 3B test update

**Files:**
- Modify: `backend/app/agents/lead_banker.py` — replace `dispatch_portfolio_strategist_tool` with `dispatch_portfolio_analysts_tool`; parallel `asyncio.gather`.
- Modify: `backend/app/prompts/lead_banker.md` — portfolio_mode synthesis section with 8 sections.
- Modify: `backend/tests/test_lead_banker_portfolio_mode.py` — mock the new tool name; assert both specialists invoked.

**Goal:** Lead Banker dispatches both Portfolio Strategist AND Risk Manager in parallel. The 3B test's mock must use the new tool name and verify both specialists were called.

- [ ] **Step 1: Update the 3B test**

Read `backend/tests/test_lead_banker_portfolio_mode.py`. Replace its content with:

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
async def test_lead_banker_portfolio_mode_dispatches_both_analysts() -> None:
    """portfolio_mode uses dispatch_portfolio_analysts; both strategist + risk run."""
    captured_strategist: list[dict[str, Any]] = []
    captured_risk: list[dict[str, Any]] = []

    async def fake_strategist(**kwargs: Any) -> dict[str, Any]:
        captured_strategist.append(kwargs)
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

    async def fake_risk(**kwargs: Any) -> dict[str, Any]:
        captured_risk.append(kwargs)
        return {
            "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
            "concentration": [], "var_by_cohort": [],
            "correlations": None, "stress_results": [],
            "notes": [], "citations": [], "confidence": 0.85,
        }

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_analysts",
                           {"specialists": ["strategist", "risk"],
                            "brief": "Snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take",
                      {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tool_use("emit_section",
                      {"title": "Portfolio Snapshot",
                       "markdown": "- AAPL 100%", "citations": []}, "2"),
            _tool_use("emit_section",
                      {"title": "Risks", "markdown": "Concentrated.",
                       "citations": []}, "3"),
            _tool_use("emit_disclaimer", {}, "4"),
            _tool_use("emit_done", {}, "5"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)
        mp.setattr(lb, "run_risk_manager", fake_risk)

        deltas: list[dict[str, Any]] = []
        async for d in lb.run_lead_banker(
            user_message="How is my portfolio doing?",
            resolution=None,
            client=fake_client,
            portfolio_mode=True,
            user_id="u-1",
        ):
            deltas.append(d)

    # Both specialists called exactly once
    assert len(captured_strategist) == 1
    assert len(captured_risk) == 1
    assert captured_strategist[0]["user_id"] == "u-1"
    assert captured_risk[0]["user_id"] == "u-1"
    # Synthesis stream contains the emitted sections
    section_titles = [d.get("title") for d in deltas if d.get("type") == "section"]
    assert "Portfolio Snapshot" in section_titles


@pytest.mark.asyncio
async def test_lead_banker_dispatch_subset_only_strategist() -> None:
    """If specialists=['strategist'], risk is NOT called."""
    captured_risk: list[dict[str, Any]] = []

    async def fake_strategist(**_kwargs: Any) -> dict[str, Any]:
        return {"portfolio_id": "p-1", "portfolio_name": "X", "cohorts": [],
                "rebalance": None, "notes": [], "citations": [], "confidence": 0.5}

    async def fake_risk(**kwargs: Any) -> dict[str, Any]:
        captured_risk.append(kwargs)
        return {}

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_portfolio_analysts",
                           {"specialists": ["strategist"],
                            "brief": "Just snapshot"}, "d1"),
                 stop_reason="tool_use"),
        _message(_tool_use("emit_done", {}, "1"), stop_reason="end_turn"),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_portfolio_strategist", fake_strategist)
        mp.setattr(lb, "run_risk_manager", fake_risk)

        async for _ in lb.run_lead_banker(
            user_message="Just my portfolio snapshot",
            resolution=None, client=fake_client,
            portfolio_mode=True, user_id="u-1",
        ):
            pass

    assert captured_risk == []
```

- [ ] **Step 2: Update `lead_banker.py`**

In `backend/app/agents/lead_banker.py`:

1. Add import near the top:

```python
import asyncio
from app.agents.risk_manager import run_risk_manager
```

2. Replace the `dispatch_portfolio_strategist_tool` definition with:

```python
dispatch_portfolio_analysts_tool = Tool(
    name="dispatch_portfolio_analysts",
    description=(
        "Run both portfolio analysts in parallel. Specialists: "
        "'strategist' (snapshot, returns, rebalance) and 'risk' "
        "(concentration, VaR, correlations, stress tests). "
        "Pass both unless the user explicitly asks for only one."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {"type": "string", "enum": ["strategist", "risk"]},
                "default": ["strategist", "risk"],
            },
            "brief": {"type": "string"},
            "target_alloc": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["brief"],
    },
    impl=None,  # type: ignore[arg-type]  # handled inline
)
```

3. In `run_lead_banker`, update the tools_list construction (the portfolio_mode branch):

```python
tools_list: list[dict[str, Any]] = [_DISPATCH_TOOL, *_EMIT_TOOLS]
if portfolio_mode:
    tools_list.append({
        "name": dispatch_portfolio_analysts_tool.name,
        "description": dispatch_portfolio_analysts_tool.description,
        "input_schema": dispatch_portfolio_analysts_tool.input_schema,
    })
```

4. In the tool-dispatch loop, REPLACE the `dispatch_portfolio_strategist` branch with:

```python
if name == "dispatch_portfolio_analysts":
    if not portfolio_mode or user_id is None:
        result: dict[str, Any] = {"error": "portfolio_mode not active or user_id missing"}
    else:
        specialists = cast(list[str], args.get("specialists", ["strategist", "risk"]))
        brief = cast(str, args.get("brief", "Analyze portfolio."))
        target_alloc = args.get("target_alloc")
        coros: list[Any] = []
        names: list[str] = []
        if "strategist" in specialists:
            coros.append(run_portfolio_strategist(
                user_id=user_id, portfolio_id=None, brief=brief,
                target_alloc=target_alloc, client=client,
            ))
            names.append("strategist")
        if "risk" in specialists:
            coros.append(run_risk_manager(
                user_id=user_id, portfolio_id=None, brief=brief, client=client,
            ))
            names.append("risk")
        gathered = await asyncio.gather(*coros, return_exceptions=True)
        result = {}
        for n, r in zip(names, gathered, strict=True):
            if isinstance(r, BaseException):
                result[n] = {"error": str(r)}
            else:
                result[n] = cast(dict[str, Any], r)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": json.dumps(result, default=str),
    })
    continue
```

5. Update the portfolio_mode user_content. Find the existing branch in `run_lead_banker`:

```python
if portfolio_mode:
    user_content = (
        f"User question: {user_message}\n\n"
        "Portfolio mode is active — analyze the user's portfolio. "
        "Call dispatch_portfolio_analysts exactly once with specialists "
        "(default ['strategist', 'risk']) and a brief. You'll receive "
        "both findings dicts. Synthesize via emit_* tools per the prompt's "
        "ordering for portfolio mode.\n\n"
        "Do NOT call dispatch_specialists in portfolio mode."
    )
```

- [ ] **Step 3: Update the prompt**

Edit `backend/app/prompts/lead_banker.md`. Find the existing "## Portfolio mode" section and REPLACE it with:

```markdown
## Portfolio mode

If the user message states "Portfolio mode is active", you have ONE dispatch tool: `dispatch_portfolio_analysts`. Use it INSTEAD of `dispatch_specialists` (do not call dispatch_specialists in portfolio mode).

Workflow:

1. Call `dispatch_portfolio_analysts` once with `specialists: ["strategist", "risk"]` (the default — pass a subset only if the user explicitly asked for one). Pass a `brief` and, if the user stated a target allocation, `target_alloc` like `{"USD:equity_etf": 0.5, "INR:equity_etf": 0.3, "USD:crypto": 0.2}` (must sum to 1.0).
2. You'll receive a dict with `strategist` and `risk` sub-findings (one or both may be present; either may have an `error` field if it failed).
3. Synthesize into `emit_*` tools in this order:
   1. `emit_quick_take` — one sentence
   2. `emit_section` **Portfolio Snapshot** — from `strategist.cohorts` (weights, totals, gain%)
   3. `emit_section` **Returns vs Benchmark** — from `strategist.cohorts` (1mo/3mo/1y vs benchmark)
   4. `emit_section` **Risk Metrics** — from `strategist.cohorts` (sharpe, beta, max drawdown)
   5. `emit_section` **Concentration** — from `risk.concentration` (only if any flags)
   6. `emit_section` **Value at Risk** — from `risk.var_by_cohort`
   7. `emit_section` **Correlations** — from `risk.correlations` (only if ≥2 tickers)
   8. `emit_section` **Stress Tests** — from `risk.stress_results` (all 3 scenarios)
   9. `emit_section` **Rebalance Plan** — from `strategist.rebalance` (only if user asked)
   10. `emit_section` **Risks** — combine concentration warnings + missing-price notes + `risk.notes` + `strategist.notes`
   11. `emit_recommendation` — only if user asked for rebalance
   12. `emit_disclaimer`
   13. `emit_done`

Discipline in portfolio mode:
- Do NOT emit `emit_stock_card` — there's no single ticker.
- Cohorts are SEPARATE: USD-equity and USD-crypto are NOT the same cohort even though both quote in USD.
- Never claim to convert cohorts in the snapshot. Cross-cohort comparison is only valid inside the rebalance plan or VaR/concentration analysis, where the assumed FX rate is stated.
- If a specialist returned an `error` field, surface it in **Risks** rather than skipping the section.
- The portfolio analysis succeeds if AT LEAST ONE specialist returned findings.
```

- [ ] **Step 4: Run all relevant tests**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_lead_banker_portfolio_mode.py tests/test_lead_banker_agent.py -v
```

Expected: 2 new portfolio_mode tests + 4 existing lead_banker tests = 6 passed.

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 176 passing (175 prior + 2 new test cases in the rewritten portfolio_mode file, minus the 1 old test it replaces = +1 net... but the rewrite added 2 tests so net +1). Acceptable count is 175-177; verify ruff + mypy strict are clean regardless.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/agents/lead_banker.py backend/app/prompts/lead_banker.md backend/tests/test_lead_banker_portfolio_mode.py && git commit -m "feat(backend): Lead Banker dispatch_portfolio_analysts — parallel strategist + risk

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `/chat/portfolio` endpoint — combined renderer + risk sections

**Files:**
- Modify: `backend/app/routes/chat_portfolio.py` — run both specialists in parallel; new section renderers.
- Modify: `backend/tests/test_routes_chat_portfolio_endpoint.py` — fixture findings now have both strategist + risk; assert new sections appear.

**Goal:** The dedicated `/chat/portfolio` endpoint should also benefit from 3C: parallel-dispatch both specialists; render the 4 new risk sections (Concentration, Value at Risk, Correlations, Stress Tests) in addition to the 3B sections.

- [ ] **Step 1: Update the test**

Read `backend/tests/test_routes_chat_portfolio_endpoint.py`. Replace its content with:

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
async def test_chat_portfolio_streams_strategist_and_risk_sections(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    fake_strategist = {
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
        "rebalance": None, "notes": [],
        "citations": [{"source": "yfinance", "ref": "AAPL 1y"}],
        "confidence": 0.85,
    }
    fake_risk = {
        "portfolio_id": "00000000-0000-0000-0000-000000000001",
        "portfolio_name": "My Portfolio",
        "concentration": [
            {"position_id": "p1", "ticker": "AAPL",
             "weight_pct": 1.0, "threshold_pct": 0.10, "severity": "critical"},
        ],
        "var_by_cohort": [
            {"cohort": ["USD", "equity_etf"], "confidence": 0.95,
             "horizon_days": 10, "methodology": "historical",
             "var_pct": -0.07, "var_native": -140.0,
             "insufficient_history": False},
        ],
        "correlations": {"tickers": ["AAPL"], "matrix": [[1.0]], "excluded": []},
        "stress_results": [
            {"scenario": "equity_-20%",
             "per_position": [{"ticker": "AAPL", "before_native": 2000.0,
                               "after_native": 1600.0, "delta_native": -400.0,
                               "delta_pct": -20.0}],
             "by_cohort": [],
             "total_delta_usd": -400.0, "assumed_fx": {}},
        ],
        "notes": [], "citations": [], "confidence": 0.85,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_strategist),
    ), patch(
        "app.routes.chat_portfolio.run_risk_manager",
        AsyncMock(return_value=fake_risk),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "Full analysis"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "event: progress" in body
    assert "Portfolio Snapshot" in body
    assert "Concentration" in body
    assert "Value at Risk" in body
    assert "Stress Tests" in body
    assert "AAPL" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_handles_specialist_failure(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """If Risk Manager fails, strategist's sections still ship + an error note appears."""
    fake_strategist = {
        "portfolio_id": "p-1", "portfolio_name": "My Portfolio",
        "cohorts": [
            {"cohort": ["USD", "equity_etf"], "positions_count": 1,
             "total_value_native": 1000.0, "total_cost_native": 1000.0,
             "gain_pct": 0.0, "weights": {"X": 1.0},
             "returns_1mo": 0.0, "returns_3mo": 0.0, "returns_1y": 0.0,
             "benchmark_ticker": "^GSPC", "benchmark_returns_1y": 0.0,
             "sharpe_1y": None, "beta_1y": None, "max_drawdown_1y": None,
             "prices_partial": False},
        ],
        "rebalance": None, "notes": [], "citations": [], "confidence": 0.7,
    }
    with patch(
        "app.routes.chat_portfolio.run_portfolio_strategist",
        AsyncMock(return_value=fake_strategist),
    ), patch(
        "app.routes.chat_portfolio.run_risk_manager",
        AsyncMock(side_effect=RuntimeError("risk module failed")),
    ):
        r = await client.post(
            "/chat/portfolio",
            json={"portfolio_id": None, "message": "Full analysis"},
            headers=_bearer(make_token(TEST_USER_ID)),
        )
    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "risk module failed" in body or "Risk analysis unavailable" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_portfolio_requires_auth(client: AsyncClient) -> None:
    r = await client.post("/chat/portfolio", json={"message": "hi"})
    assert r.status_code == 401
```

- [ ] **Step 2: Update the endpoint**

Read `backend/app/routes/chat_portfolio.py`. Apply these edits:

1. Replace the imports block at the top with:

```python
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.portfolio_strategist import run_portfolio_strategist
from app.agents.risk_manager import run_risk_manager
from app.core.auth import get_current_user
```

2. Add 4 new section renderers above `_stream_findings`:

```python
def _render_concentration_section(flags: list[dict[str, Any]]) -> str:
    if not flags:
        return "_No concentration flags. Largest single position is under 10% of the portfolio._"
    lines: list[str] = []
    for f in flags:
        weight = f.get("weight_pct", 0) * 100
        severity = f.get("severity", "warn")
        marker = "🔴" if severity == "critical" else "🟡"
        lines.append(f"- {marker} **{f.get('ticker')}**: {weight:.1f}% — {severity}")
    return "\n".join(lines)


def _render_var_section(var_by_cohort: list[dict[str, Any]]) -> str:
    if not var_by_cohort:
        return "_VaR unavailable._"
    lines: list[str] = []
    for v in var_by_cohort:
        cohort = v.get("cohort", ["?", "?"])
        label = _cohort_label(cohort)
        conf = v.get("confidence", 0.95)
        horizon = v.get("horizon_days", 10)
        if v.get("insufficient_history") or v.get("var_pct") is None:
            lines.append(f"- **{label}** — insufficient history (need ≥100 daily closes)")
            continue
        pct = v["var_pct"] * 100
        native = v.get("var_native")
        currency = cohort[0]
        native_str = (
            _format_currency(abs(native), currency) if native is not None else "—"
        )
        lines.append(
            f"- **{label}** — {int(conf * 100)}% / {horizon}-day VaR: "
            f"{pct:+.1f}% (≈ {native_str} loss)"
        )
    return "\n".join(lines)


def _render_correlations_section(corr: dict[str, Any] | None) -> str:
    if not corr or not corr.get("tickers"):
        return "_Not enough positions with overlapping history to compute correlations._"
    tickers = corr["tickers"]
    matrix = corr["matrix"]
    excluded = corr.get("excluded", [])
    if len(tickers) < 2:
        return "_Need at least 2 positions with overlapping history._"
    # Top-3 highest absolute correlations (excluding diagonal)
    pairs: list[tuple[str, str, float]] = []
    for i, ti in enumerate(tickers):
        for j, tj in enumerate(tickers):
            if i >= j:
                continue
            pairs.append((ti, tj, matrix[i][j]))
    pairs.sort(key=lambda p: -abs(p[2]))
    lines: list[str] = ["**Top correlations:**"]
    for ti, tj, r in pairs[:5]:
        lines.append(f"- {ti} ↔ {tj}: {r:+.2f}")
    if excluded:
        lines.append(f"_Excluded (insufficient history): {', '.join(excluded)}_")
    return "\n".join(lines)


def _render_stress_section(stress_results: list[dict[str, Any]]) -> str:
    if not stress_results:
        return "_No stress test results._"
    lines: list[str] = []
    for s in stress_results:
        scenario = s.get("scenario", "?")
        total = s.get("total_delta_usd", 0)
        per_pos = s.get("per_position", [])
        worst = (
            min(per_pos, key=lambda p: p.get("delta_native", 0))
            if per_pos else None
        )
        worst_str = ""
        if worst:
            worst_str = (
                f" — worst hit: **{worst['ticker']}** "
                f"({worst.get('delta_pct', 0):+.1f}%)"
            )
        lines.append(
            f"- **{scenario}**: portfolio Δ ≈ "
            f"{_format_currency(total, 'USD')} (USD-eq){worst_str}"
        )
    return "\n".join(lines)
```

3. Rewrite `_stream_findings` to receive a combined dict `{strategist: {...}, risk: {...}, errors: {...}}` and emit 8 sections:

```python
async def _stream_findings(combined: dict[str, Any]) -> AsyncIterator[bytes]:
    yield (
        b"event: progress\ndata: "
        + json.dumps({"step": "analyzing_portfolio"}).encode()
        + b"\n\n"
    )

    strategist = combined.get("strategist") or {}
    risk = combined.get("risk") or {}
    errors = combined.get("errors", {})

    cohorts = strategist.get("cohorts", [])
    if not cohorts and not risk.get("concentration") and not risk.get("stress_results"):
        msg = (strategist.get("notes") or risk.get("notes") or ["No portfolio data."])[0]
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Portfolio",
                          "markdown": msg, "citations": []}).encode()
            + b"\n\n"
        )
    else:
        if cohorts:
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
        if risk:
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Concentration",
                              "markdown": _render_concentration_section(
                                  risk.get("concentration", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Value at Risk",
                              "markdown": _render_var_section(
                                  risk.get("var_by_cohort", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Correlations",
                              "markdown": _render_correlations_section(
                                  risk.get("correlations")),
                              "citations": []}).encode()
                + b"\n\n"
            )
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Stress Tests",
                              "markdown": _render_stress_section(
                                  risk.get("stress_results", [])),
                              "citations": []}).encode()
                + b"\n\n"
            )
        if strategist.get("rebalance"):
            yield (
                b"event: delta\ndata: "
                + json.dumps({"type": "section", "title": "Rebalance Plan",
                              "markdown": _render_rebalance_section(strategist["rebalance"]),
                              "citations": []}).encode()
                + b"\n\n"
            )
    combined_notes: list[str] = []
    if strategist.get("notes"):
        combined_notes.extend(strategist["notes"])
    if risk.get("notes"):
        combined_notes.extend(risk["notes"])
    for source, err in errors.items():
        combined_notes.append(f"{source.capitalize()} analysis unavailable: {err}")
    if combined_notes:
        yield (
            b"event: delta\ndata: "
            + json.dumps({"type": "section", "title": "Notes",
                          "markdown": "\n".join(f"- {n}" for n in combined_notes),
                          "citations": []}).encode()
            + b"\n\n"
        )
    yield b"event: done\ndata: {}\n\n"
```

4. Update the route handler to run both specialists in parallel and handle errors:

```python
@router.post("/chat/portfolio")
async def chat_portfolio(
    req: ChatPortfolioRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    user_id = user["sub"]
    brief = req.message or "Snapshot of my current portfolio."
    results = await asyncio.gather(
        run_portfolio_strategist(
            user_id=user_id, portfolio_id=req.portfolio_id,
            brief=brief, target_alloc=req.target_alloc,
        ),
        run_risk_manager(
            user_id=user_id, portfolio_id=req.portfolio_id, brief=brief,
        ),
        return_exceptions=True,
    )
    combined: dict[str, Any] = {"errors": {}}
    if isinstance(results[0], BaseException):
        combined["errors"]["strategist"] = str(results[0])
    else:
        combined["strategist"] = results[0]
    if isinstance(results[1], BaseException):
        combined["errors"]["risk"] = str(results[1])
    else:
        combined["risk"] = results[1]
    return StreamingResponse(_stream_findings(combined), media_type="text/event-stream")
```

- [ ] **Step 3: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run pytest tests/test_routes_chat_portfolio_endpoint.py -v
```

Expected: 3 passed (one new + two from the rewritten file).

- [ ] **Step 4: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: ~178 passing.

- [ ] **Step 5: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/app/routes/chat_portfolio.py backend/tests/test_routes_chat_portfolio_endpoint.py && git commit -m "feat(backend): /chat/portfolio parallel dispatch + 4 risk section renderers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Update backend e2e + push

**Files:**
- Modify: `backend/tests/test_routes_e2e_portfolio_analysis.py` — update Lead Banker mock to use `dispatch_portfolio_analysts` and return both findings.

**Goal:** Update the 3B end-to-end test so it still passes with the new Lead Banker tool. Then push.

- [ ] **Step 1: Update the test**

Read `backend/tests/test_routes_e2e_portfolio_analysis.py`. Update the Lead Banker mock responses so:
1. Turn 1: emits `_tu("dispatch_portfolio_analysts", {"specialists": ["strategist", "risk"], "brief": "Snapshot"}, "d1")` instead of `dispatch_portfolio_strategist`.
2. Add a Risk Manager mock response sequence (mirroring the strategist one):
   ```
   _msg(_tu("concentration_check", {}, "c1")),
   _msg(_tu("submit_risk_findings", {...empty findings...}, "rf1"), stop_reason="tool_use"),
   ```
3. Re-order `all_responses` to interleave correctly:
   - LB turn 1 (dispatch_portfolio_analysts)
   - Strategist sequence (get_holdings → submit_portfolio_findings) — 2 messages
   - Risk Manager sequence (concentration_check → submit_risk_findings) — 2 messages
   - LB turn 2 (emit_*)

   Order: `[lb_t1, strat_get_holdings, strat_submit, risk_conc, risk_submit, lb_t2]`

   NOTE: asyncio.gather runs both specialists concurrently. The mock's side_effect list is sequential — each call to `client.messages.create` consumes the next item. With concurrent execution, the consumption order isn't strictly predictable. To make this deterministic, the test should either:
   - Use a different mock for each sub-agent (patch `app.agents.portfolio_strategist.get_client` and `app.agents.risk_manager.get_client` separately), OR
   - Use a single shared mock but observe that asyncio's default behaviour is to schedule sub-tasks in submission order; the gather will dispatch strategist first, then risk, but their internal turns may interleave.

   For determinism, prefer the separate-mock approach. Patch `app.agents.portfolio_strategist.get_client` with a strategist-only mock and `app.agents.risk_manager.get_client` with a risk-only mock. Lead Banker uses its own mock via `app.routes.chat.get_client`.

The updated test:

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


def _msg(*blocks: dict[str, Any], stop_reason: str = "tool_use") -> Any:
    m = MagicMock()
    m.content = list(blocks)
    m.stop_reason = stop_reason
    m.usage = MagicMock(input_tokens=10, output_tokens=20,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return m


def _tu(name: str, args: dict[str, Any], tid: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


@pytest.mark.asyncio
async def test_chat_stream_portfolio_end_to_end_with_risk(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    """Portfolio query → Lead Banker → dispatch_portfolio_analysts → both specialists → emit sections."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": UUID("00000000-0000-0000-0000-0000000000a1"),
         "portfolio_id": UUID(PID), "ticker": "AAPL", "market": "US",
         "asset_class": "equity", "quantity": 50, "cost_basis": 150.0,
         "currency": "USD", "opened_at": date(2024, 6, 1), "created_at": None},
    ])
    conn.fetchrow = AsyncMock(return_value={"id": UUID(PID)})

    fake_prices: dict[tuple[str, str], Price] = {
        ("AAPL", "US"): Price(ticker="AAPL", market="US", price=200.0,
                              currency="USD", as_of=datetime(2026, 5, 17)),
    }

    # Separate clients for each agent
    lb_client = MagicMock()
    lb_client.messages.create = AsyncMock(side_effect=[
        _msg(_tu("dispatch_portfolio_analysts",
                 {"specialists": ["strategist", "risk"], "brief": "Snapshot"},
                 "d1"), stop_reason="tool_use"),
        _msg(
            _tu("emit_quick_take",
                {"signal": "hold", "qualifier": "Diversified."}, "1"),
            _tu("emit_section", {"title": "Portfolio Snapshot",
                                  "markdown": "USD: $10k +33%", "citations": []}, "2"),
            _tu("emit_section", {"title": "Concentration",
                                  "markdown": "AAPL 100% — critical",
                                  "citations": []}, "3"),
            _tu("emit_section", {"title": "Risks", "markdown": "concentrated.",
                                  "citations": []}, "4"),
            _tu("emit_disclaimer", {}, "5"),
            _tu("emit_done", {}, "6"),
            stop_reason="end_turn",
        ),
    ])

    strategist_client = MagicMock()
    strategist_client.messages.create = AsyncMock(side_effect=[
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
    ])

    risk_client = MagicMock()
    risk_client.messages.create = AsyncMock(side_effect=[
        _msg(_tu("concentration_check", {}, "c1")),
        _msg(_tu("submit_risk_findings", {
            "portfolio_id": PID,
            "portfolio_name": "Test",
            "concentration": [{"position_id": None, "ticker": "AAPL",
                               "weight_pct": 1.0, "threshold_pct": 0.10,
                               "severity": "critical"}],
            "var_by_cohort": [],
            "correlations": None,
            "stress_results": [],
            "notes": [], "citations": [], "confidence": 0.85,
        }, "r1"), stop_reason="tool_use"),
    ])

    with patch("app.db.portfolios.acquire_conn", return_value=_async_cm(conn)), \
         patch("app.tools.portfolio.get_prices",
               AsyncMock(return_value=fake_prices)), \
         patch("app.tools.portfolio._fetch_price_history_for_position",
               AsyncMock(return_value=[])), \
         patch("app.tools.risk._fetch_price_history",
               AsyncMock(return_value=[])), \
         patch("app.data.benchmarks.get_benchmark_history",
               AsyncMock(return_value=[])), \
         patch("app.tools.risk.get_usdinr",
               AsyncMock(return_value=None)), \
         patch("app.routes.chat.get_client", return_value=lb_client), \
         patch("app.agents.portfolio_strategist.get_client",
               return_value=strategist_client), \
         patch("app.agents.risk_manager.get_client", return_value=risk_client):
        r = await client.post(
            "/chat/stream",
            json={"content": "How is my portfolio doing?"},
            headers={"Authorization": f"Bearer {make_token(TEST_USER_ID)}"},
        )

    assert r.status_code == 200
    body = r.text
    assert "Portfolio Snapshot" in body
    assert "Concentration" in body
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

Expected: ~178 passing. ruff + mypy strict clean.

- [ ] **Step 4: Commit**

```bash
cd /Users/rakhisinha/Stylobate && git add backend/tests/test_routes_e2e_portfolio_analysis.py && git commit -m "test(backend): update e2e — dispatch_portfolio_analysts runs strategist + risk

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Push all Phase 3C commits**

```bash
cd /Users/rakhisinha/Stylobate && git push origin main
```

Render will auto-deploy. The deployed backend will continue to 500 on portfolio endpoints until the DB password is restored (deferred from 3B); Phase 3C code is shippable independently.

## Report

Summarize the final state:
- Test count delta from 155 → 178 (approximate; verify with `uv run pytest --collect-only -q | tail -3`)
- All 6 task commits on `main`
- ruff + mypy strict clean
- Render deploy status
- Phase 3 complete per the parent spec § 15

---

## End-of-Phase 3C acceptance checklist

- [ ] Backend tests: ~178 passing
- [ ] ruff + mypy strict clean
- [ ] All 4 Playwright tests still pass (no frontend changes; new sections render via existing chat UI)
- [ ] All 6 task commits on `main`, pushed
- [ ] Render auto-deploy green
- [ ] Live UAT (deferred to when DB password restored): the analyze flow now shows Concentration / VaR / Correlations / Stress Tests sections in addition to 3B's Snapshot / Returns / Risk Metrics

**Phase 3 of the parent spec is then COMPLETE** ("user can input holdings and get a health check"). Phase 4 (Screener — `screen_stocks`, `theme_to_universe`) is the next major plan.
