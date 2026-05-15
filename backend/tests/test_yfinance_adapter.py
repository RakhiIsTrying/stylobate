# backend/tests/test_yfinance_adapter.py
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def fake_ticker() -> MagicMock:
    mock = MagicMock()
    mock.info = {
        "shortName": "Apple Inc.",
        "longName": "Apple Inc.",
        "currency": "USD",
        "marketCap": 3_500_000_000_000,
        "trailingPE": 29.5,
        "priceToBook": 50.1,
        "priceToSalesTrailing12Months": 8.8,
        "returnOnEquity": 1.45,
        "debtToEquity": 152.0,
        "currentRatio": 0.98,
        "profitMargins": 0.255,
        "revenueGrowth": 0.062,
    }
    mock.income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2024-09-28"): {
                "Total Revenue": 391_035_000_000,
                "Net Income": 93_736_000_000,
            },
            pd.Timestamp("2023-09-30"): {
                "Total Revenue": 383_285_000_000,
                "Net Income": 96_995_000_000,
            },
        }
    )
    mock.cashflow = pd.DataFrame(
        {
            pd.Timestamp("2024-09-28"): {
                "Operating Cash Flow": 118_254_000_000,
                "Free Cash Flow": 108_807_000_000,
            },
            pd.Timestamp("2023-09-30"): {
                "Operating Cash Flow": 110_543_000_000,
                "Free Cash Flow": 99_584_000_000,
            },
        }
    )
    mock.fast_info = MagicMock(last_price=225.30, market_cap=3_500_000_000_000)
    return mock


@pytest.mark.asyncio
async def test_fetch_ticker_info_maps_yfinance_fields(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_ticker_info
        info = await fetch_ticker_info("AAPL")
    assert info.ticker == "AAPL"
    assert info.name == "Apple Inc."
    assert info.currency == "USD"
    assert info.market_cap == 3_500_000_000_000


@pytest.mark.asyncio
async def test_fetch_financials_returns_periods_in_order(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_financials
        results = await fetch_financials("AAPL", periods=2)
    assert [r.period for r in results] == ["FY2024", "FY2023"]
    assert results[0].revenue == 391_035_000_000
    assert results[0].net_income == 93_736_000_000
    assert results[0].operating_cash_flow == 118_254_000_000
    assert results[0].free_cash_flow == 108_807_000_000
    assert results[0].currency == "USD"


@pytest.mark.asyncio
async def test_fetch_key_ratios_picks_correct_fields(fake_ticker: MagicMock) -> None:
    with patch("app.data.yfinance_adapter._make_ticker", return_value=fake_ticker):
        from app.data.yfinance_adapter import fetch_key_ratios
        ratios = await fetch_key_ratios("AAPL")
    assert ratios.pe_ttm == 29.5
    assert ratios.pb == 50.1
    assert ratios.roe == 1.45
    assert ratios.net_margin == 0.255
    assert ratios.revenue_growth_yoy == 0.062


@pytest.mark.asyncio
async def test_fetch_returns_none_for_missing_fields() -> None:
    bare = MagicMock()
    bare.info = {"shortName": "X", "currency": "USD"}
    bare.income_stmt = pd.DataFrame()
    bare.cashflow = pd.DataFrame()
    bare.fast_info = MagicMock(last_price=None, market_cap=None)
    with patch("app.data.yfinance_adapter._make_ticker", return_value=bare):
        from app.data.yfinance_adapter import fetch_key_ratios
        r = await fetch_key_ratios("X")
    assert r.pe_ttm is None
    assert r.net_margin is None
