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
