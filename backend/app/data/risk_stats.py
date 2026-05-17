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
    # Precompute aligned returns for every pair. A ticker is included if its own
    # history meets min_overlap (so it appears on the diagonal); pair overlaps
    # populate off-diagonal entries when available.
    for i, ti in enumerate(tickers):
        hi = price_history.get(ti)
        if not hi or len(_daily_log_returns(hi)) < min_overlap:
            excluded.append(ti)
            continue
        included.append(ti)
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
    matrix: list[list[float]] = []
    for ti in included:
        row: list[float] = []
        for tj in included:
            if ti == tj:
                row.append(1.0)
                continue
            ri_pair = aligned_returns.get(ti, {}).get(tj)
            rj_pair = aligned_returns.get(tj, {}).get(ti)
            if ri_pair is None or rj_pair is None:
                row.append(0.0)
                continue
            r = _pearson(ri_pair, rj_pair)
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
