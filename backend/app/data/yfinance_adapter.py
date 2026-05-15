# backend/app/data/yfinance_adapter.py
import asyncio
from datetime import UTC, date, datetime
from typing import Any

import yfinance as yf
from pydantic import BaseModel


def _make_ticker(ticker: str) -> Any:
    """Indirection so tests can patch this exact name."""
    return yf.Ticker(ticker)


def _yf_symbol(ticker: str, market: str) -> str:
    """Map our canonical ticker to the yfinance symbol.

    yfinance crypto symbols use a -USD suffix (BTC -> BTC-USD); our
    canonical form drops the suffix. US and Indian tickers pass through
    unchanged. If the ticker already includes -USD, don't double it.
    """
    if market == "CRYPTO":
        if "-" in ticker:
            return ticker
        return f"{ticker}-USD"
    return ticker


class TickerInfo(BaseModel):
    ticker: str
    name: str
    currency: str
    market_cap: int | None
    last_price: float | None


class Financials(BaseModel):
    ticker: str
    period: str
    revenue: float | None
    net_income: float | None
    operating_cash_flow: float | None
    free_cash_flow: float | None
    currency: str


class KeyRatios(BaseModel):
    ticker: str
    as_of: date
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    roe: float | None
    roic: float | None
    fcf_yield: float | None
    debt_to_equity: float | None
    current_ratio: float | None
    net_margin: float | None
    revenue_growth_yoy: float | None


class Bar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class NewsItem(BaseModel):
    title: str
    publisher: str
    url: str
    published_at: datetime
    summary: str | None = None
    related_tickers: list[str] = []


def _period_label(ts: Any) -> str:
    """Turn a pandas Timestamp into a fiscal-year label.

    Uses calendar year. Accurate for calendar-year and most US fiscal-year
    companies. May mislabel non-calendar fiscal years (e.g., Indian FY
    ending Mar 31, UK companies ending Apr 5). yfinance does not expose
    fiscal-year mappings.
    """
    year = getattr(ts, "year", None)
    if year is None:
        return "unknown"
    return f"FY{year}"


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        f = float(value)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


async def fetch_ticker_info(ticker: str, market: str = "US") -> TickerInfo:
    def _sync() -> TickerInfo:
        t = _make_ticker(_yf_symbol(ticker, market))
        info = t.info or {}
        fast = getattr(t, "fast_info", None)
        last_price = _float_or_none(getattr(fast, "last_price", None))
        market_cap = info.get("marketCap") or getattr(fast, "market_cap", None)
        return TickerInfo(
            ticker=ticker.upper(),
            name=info.get("shortName") or info.get("longName") or ticker.upper(),
            currency=info.get("currency", "USD"),
            market_cap=int(market_cap) if market_cap else None,
            last_price=last_price,
        )

    return await asyncio.to_thread(_sync)


async def fetch_financials(
    ticker: str, periods: int = 4, market: str = "US"
) -> list[Financials]:
    def _sync() -> list[Financials]:
        t = _make_ticker(_yf_symbol(ticker, market))
        info = t.info or {}
        currency = info.get("currency", "USD")
        income = getattr(t, "income_stmt", None)
        cashflow = getattr(t, "cashflow", None)
        if income is None or income.empty:
            return []
        out: list[Financials] = []
        cols = list(income.columns)[:periods]
        for col in cols:
            rev = _float_or_none(income[col].get("Total Revenue"))
            ni = _float_or_none(income[col].get("Net Income"))
            ocf = None
            fcf = None
            if cashflow is not None and not cashflow.empty and col in cashflow.columns:
                ocf = _float_or_none(cashflow[col].get("Operating Cash Flow"))
                fcf = _float_or_none(cashflow[col].get("Free Cash Flow"))
            out.append(
                Financials(
                    ticker=ticker.upper(),
                    period=_period_label(col),
                    revenue=rev,
                    net_income=ni,
                    operating_cash_flow=ocf,
                    free_cash_flow=fcf,
                    currency=currency,
                )
            )
        return out

    return await asyncio.to_thread(_sync)


async def fetch_key_ratios(ticker: str, market: str = "US") -> KeyRatios:
    def _sync() -> KeyRatios:
        t = _make_ticker(_yf_symbol(ticker, market))
        info = t.info or {}
        return KeyRatios(
            ticker=ticker.upper(),
            as_of=date.today(),
            pe_ttm=_float_or_none(info.get("trailingPE")),
            pb=_float_or_none(info.get("priceToBook")),
            ps_ttm=_float_or_none(info.get("priceToSalesTrailing12Months")),
            roe=_float_or_none(info.get("returnOnEquity")),
            # ROIC isn't exposed by yfinance; ROA is the closest proxy.
            # Downstream consumers should treat KeyRatios.roic as approximate.
            roic=_float_or_none(info.get("returnOnAssets")),
            # FCF yield not exposed by yfinance; Phase 2 will compute it
            # from free_cash_flow / market_cap.
            fcf_yield=None,
            debt_to_equity=_float_or_none(info.get("debtToEquity")),
            current_ratio=_float_or_none(info.get("currentRatio")),
            net_margin=_float_or_none(info.get("profitMargins")),
            revenue_growth_yoy=_float_or_none(info.get("revenueGrowth")),
        )

    return await asyncio.to_thread(_sync)


async def fetch_price_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    market: str = "US",
) -> list[Bar]:
    """OHLCV bars for the ticker. period/interval per yfinance convention.

    period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max.
    interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo.
    """

    def _sync() -> list[Bar]:
        t = _make_ticker(_yf_symbol(ticker, market))
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
                    volume=int(v)
                    if v is not None and not (isinstance(v, float) and v != v)
                    else 0,
                )
            )
        return out

    return await asyncio.to_thread(_sync)


async def fetch_news_for_ticker(
    ticker: str, limit: int = 10, market: str = "US"
) -> list[NewsItem]:
    """Recent news headlines for the ticker, via Yahoo Finance.

    yfinance returns at most ~20 recent items per ticker; we slice to `limit`.
    """

    def _sync() -> list[NewsItem]:
        t = _make_ticker(_yf_symbol(ticker, market))
        raw = getattr(t, "news", None) or []
        out: list[NewsItem] = []
        for item in raw[:limit]:
            ts = item.get("providerPublishTime")
            if ts is None:
                continue
            try:
                pub_at = datetime.fromtimestamp(int(ts), tz=UTC)
            except (TypeError, ValueError):
                continue
            out.append(
                NewsItem(
                    title=str(item.get("title", "")),
                    publisher=str(item.get("publisher", "")),
                    url=str(item.get("link", "")),
                    published_at=pub_at,
                    summary=item.get("summary"),
                    related_tickers=item.get("relatedTickers", []) or [],
                )
            )
        return out

    return await asyncio.to_thread(_sync)


# Standard SPDR sector ETFs covering the S&P 500
SPDR_SECTOR_ETFS: dict[str, str] = {
    "XLK": "technology",
    "XLF": "financials",
    "XLV": "healthcare",
    "XLE": "energy",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLI": "industrials",
    "XLB": "materials",
    "XLU": "utilities",
    "XLRE": "real_estate",
    "XLC": "communication_services",
}


async def fetch_sector_etf_returns(
    period: str = "1mo",
    tickers: list[str] | None = None,
) -> dict[str, float]:
    """Period return for each sector ETF: (last_close - first_close) / first_close."""
    syms = tickers if tickers is not None else list(SPDR_SECTOR_ETFS.keys())
    out: dict[str, float] = {}
    for sym in syms:
        bars = await fetch_price_history(sym, period=period, interval="1d")
        if len(bars) < 2:
            continue
        first = bars[0].close
        last = bars[-1].close
        if first == 0:
            continue
        out[sym] = (last - first) / first
    return out
