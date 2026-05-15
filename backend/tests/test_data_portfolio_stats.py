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
    # 252 trading days lookback; price 100 → 200 = +100% (need 253 entries for full lookback)
    history = {"AAPL": _days(253, start=100.0, step=100 / 252)}
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
    h: list[tuple[date, float]] = [
        (date(2025, 5, 15) + timedelta(days=i), float(p))
        for i, p in enumerate([100] * 50 + [150] * 50 + [50] * 50 + [80] * 102)
    ]
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
