# backend/app/tools/technical.py
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from app.data.yfinance_adapter import Bar
from app.data.yfinance_adapter import fetch_price_history as _fetch_price_history
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
    market: str = "US",
) -> PriceHistoryResult:
    bars = await _fetch_price_history(
        ticker=ticker, period=period, interval=interval, market=market
    )
    return PriceHistoryResult(ticker=ticker.upper(), bars=bars)


get_price_history_tool = Tool(
    name="get_price_history",
    description=(
        "OHLCV bars for the ticker. period: 1d/5d/1mo/3mo/6mo/1y/2y/5y/10y/ytd/max. "
        "interval: 1m/5m/15m/30m/1h/1d/1wk/1mo. Default: 1y daily. "
        "Pass market='CRYPTO' for crypto tickers (BTC, ETH, etc.) so the adapter "
        "appends -USD; 'IN' for Indian tickers; 'US' (default) otherwise."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
            "interval": {"type": "string", "default": "1d"},
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
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
    market: str = "US",
) -> IndicatorsResult:
    indicators = indicators or ["sma20", "sma200", "ema12", "ema26", "rsi14", "macd", "bbands20"]
    bars = await _fetch_price_history(
        ticker=ticker, period=period, interval="1d", market=market
    )
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
        "Defaults to a useful core set if `indicators` omitted. "
        "Pass market='CRYPTO' for crypto tickers (BTC, ETH, etc.); 'IN' for Indian "
        "tickers; 'US' (default) otherwise."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "indicators": {"type": "array", "items": {"type": "string"}},
            "period": {"type": "string", "default": "1y"},
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
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
    market: str = "US",
) -> PatternsResult:
    """Find prior swing lows (support) and highs (resistance).

    A local low: bar `i` whose `low` is the smallest in [i-window, i+window].
    Group nearby lows into levels (within `tolerance` of each other) and
    count touches. Same for highs (resistance).
    """
    bars = await _fetch_price_history(
        ticker=ticker, period=period, interval="1d", market=market
    )
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
        "the lookback period. Returns clustered levels with touch counts. "
        "Pass market='CRYPTO' for crypto tickers (BTC, ETH, etc.); 'IN' for Indian "
        "tickers; 'US' (default) otherwise."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
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
    market: str = "US",
) -> VolumeProfileResult:
    bars = await _fetch_price_history(
        ticker=ticker, period=period, interval="1d", market=market
    )
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
        "Returns the top 5 high-volume price nodes. "
        "Pass market='CRYPTO' for crypto tickers (BTC, ETH, etc.); 'IN' for Indian "
        "tickers; 'US' (default) otherwise."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "default": "1y"},
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
        },
        "required": ["ticker"],
    },
    impl=_impl_get_volume_profile,
)
