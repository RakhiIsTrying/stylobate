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
    """Pct return of last value vs value `days` rows ago. Percent (x100). None if too short."""
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
        ticker_map: dict[date, float] = {dt: v for dt, v in h}
        by_ticker[p["ticker"]] = ticker_map
        common_dates = (
            set(ticker_map) if common_dates is None else (common_dates & set(ticker_map))
        )
    if not common_dates or not by_ticker:
        return []
    out: list[tuple[date, float]] = []
    qty_by_ticker = {p["ticker"]: float(p["quantity"]) for p in positions}
    for dt in sorted(common_dates):
        total = 0.0
        for ticker, day_map in by_ticker.items():
            total += qty_by_ticker[ticker] * day_map[dt]
        out.append((dt, total))
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
