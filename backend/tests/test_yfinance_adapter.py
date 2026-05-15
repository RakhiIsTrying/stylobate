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


@pytest.mark.asyncio
async def test_fetch_price_history_returns_typed_bars() -> None:
    history_df = pd.DataFrame(
        {
            "Open": [220.0, 222.0, 218.5],
            "High": [225.0, 224.0, 222.0],
            "Low": [219.0, 220.0, 217.0],
            "Close": [224.0, 221.0, 220.5],
            "Volume": [50_000_000, 48_000_000, 52_000_000],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp("2026-05-12"), pd.Timestamp("2026-05-13"), pd.Timestamp("2026-05-14")]
        ),
    )
    mock_ticker = MagicMock()
    mock_ticker.history = MagicMock(return_value=history_df)
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_price_history
        bars = await fetch_price_history("AAPL", period="5d", interval="1d")
    assert len(bars) == 3
    assert bars[0].date.isoformat() == "2026-05-12"
    assert bars[0].open == 220.0
    assert bars[2].close == 220.5
    assert bars[1].volume == 48_000_000
    mock_ticker.history.assert_called_once_with(period="5d", interval="1d")


@pytest.mark.asyncio
async def test_fetch_price_history_empty_returns_empty_list() -> None:
    mock_ticker = MagicMock()
    mock_ticker.history = MagicMock(return_value=pd.DataFrame())
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_price_history
        bars = await fetch_price_history("X")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_news_returns_typed_items() -> None:
    raw_news = [
        {
            "uuid": "abc",
            "title": "Apple unveils new iPhone",
            "publisher": "Reuters",
            "link": "https://reuters.com/aapl-iphone",
            "providerPublishTime": 1747500000,  # 2025-05-17 ~16:00 UTC
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
        {
            "uuid": "def",
            "title": "AAPL Q2 earnings beat",
            "publisher": "Bloomberg",
            "link": "https://bloomberg.com/aapl-q2",
            "providerPublishTime": 1747600000,
            "summary": "Apple reported Q2 EPS above consensus.",
            "relatedTickers": ["AAPL"],
        },
    ]
    mock_ticker = MagicMock()
    mock_ticker.news = raw_news
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("AAPL", limit=5)
    assert len(items) == 2
    assert items[0].title == "Apple unveils new iPhone"
    assert items[0].publisher == "Reuters"
    assert items[0].url == "https://reuters.com/aapl-iphone"
    assert items[1].summary == "Apple reported Q2 EPS above consensus."


@pytest.mark.asyncio
async def test_fetch_news_respects_limit() -> None:
    raw = [
        {"uuid": f"u{i}", "title": f"News {i}", "publisher": "X",
         "link": "http://x", "providerPublishTime": 1747000000 + i,
         "relatedTickers": ["AAPL"]}
        for i in range(20)
    ]
    mock_ticker = MagicMock()
    mock_ticker.news = raw
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("AAPL", limit=5)
    assert len(items) == 5


@pytest.mark.asyncio
async def test_fetch_news_handles_empty_list() -> None:
    mock_ticker = MagicMock()
    mock_ticker.news = []
    with patch("app.data.yfinance_adapter._make_ticker", return_value=mock_ticker):
        from app.data.yfinance_adapter import fetch_news_for_ticker
        items = await fetch_news_for_ticker("X")
    assert items == []


@pytest.mark.asyncio
async def test_fetch_sector_etf_returns_computes_period_returns() -> None:
    """fetch_sector_etf_returns calls fetch_price_history for each sector ETF
    and returns 1m % return based on first vs last close."""
    from datetime import date

    from app.data.yfinance_adapter import Bar

    def _bars_for(open_price: float, close_price: float) -> list[Bar]:
        return [
            Bar(date=date(2026, 4, 1), open=open_price, high=open_price + 1,
                low=open_price - 1, close=open_price, volume=10_000),
            Bar(date=date(2026, 5, 1), open=close_price - 1, high=close_price,
                low=close_price - 2, close=close_price, volume=10_000),
        ]

    # Tech up 10%, Financials flat, Healthcare down 5%
    sector_data = {
        "XLK": _bars_for(100.0, 110.0),  # +10%
        "XLF": _bars_for(50.0, 50.0),    # 0%
        "XLV": _bars_for(80.0, 76.0),    # -5%
    }

    async def fake_fetch(ticker: str, period: str = "1mo", interval: str = "1d") -> list[Bar]:
        return sector_data.get(ticker, [])

    with patch("app.data.yfinance_adapter.fetch_price_history", side_effect=fake_fetch):
        from app.data.yfinance_adapter import fetch_sector_etf_returns
        returns = await fetch_sector_etf_returns(period="1mo", tickers=["XLK", "XLF", "XLV"])

    assert returns["XLK"] == pytest.approx(0.10, rel=0.01)
    assert returns["XLF"] == pytest.approx(0.0, abs=0.001)
    assert returns["XLV"] == pytest.approx(-0.05, rel=0.01)
