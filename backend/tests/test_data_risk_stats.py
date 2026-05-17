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
