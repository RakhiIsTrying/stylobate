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
