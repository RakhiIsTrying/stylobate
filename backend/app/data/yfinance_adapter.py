# backend/app/data/yfinance_adapter.py
import asyncio
from datetime import date
from typing import Any

import yfinance as yf
from pydantic import BaseModel


def _make_ticker(ticker: str) -> Any:
    """Indirection so tests can patch this exact name."""
    return yf.Ticker(ticker)


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


def _period_label(ts: Any) -> str:
    """Turn a pandas Timestamp into a fiscal-year label."""
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


async def fetch_ticker_info(ticker: str) -> TickerInfo:
    def _sync() -> TickerInfo:
        t = _make_ticker(ticker)
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


async def fetch_financials(ticker: str, periods: int = 4) -> list[Financials]:
    def _sync() -> list[Financials]:
        t = _make_ticker(ticker)
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


async def fetch_key_ratios(ticker: str) -> KeyRatios:
    def _sync() -> KeyRatios:
        t = _make_ticker(ticker)
        info = t.info or {}
        return KeyRatios(
            ticker=ticker.upper(),
            as_of=date.today(),
            pe_ttm=_float_or_none(info.get("trailingPE")),
            pb=_float_or_none(info.get("priceToBook")),
            ps_ttm=_float_or_none(info.get("priceToSalesTrailing12Months")),
            roe=_float_or_none(info.get("returnOnEquity")),
            roic=_float_or_none(info.get("returnOnAssets")),  # approx; ROIC isn't in yfinance
            fcf_yield=None,
            debt_to_equity=_float_or_none(info.get("debtToEquity")),
            current_ratio=_float_or_none(info.get("currentRatio")),
            net_margin=_float_or_none(info.get("profitMargins")),
            revenue_growth_yoy=_float_or_none(info.get("revenueGrowth")),
        )

    return await asyncio.to_thread(_sync)
