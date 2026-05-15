# backend/tests/test_tools_financials.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.yfinance_adapter import Financials, KeyRatios
from app.tools.financials import FinancialsResult


@pytest.mark.asyncio
async def test_get_financials_returns_list() -> None:
    fake_data = [
        Financials(
            ticker="AAPL", period="FY2024", revenue=391_035_000_000,
            net_income=93_736_000_000, operating_cash_flow=118_254_000_000,
            free_cash_flow=108_807_000_000, currency="USD",
        ),
    ]
    with patch("app.tools.financials._fetch_financials", new=AsyncMock(return_value=fake_data)):
        from app.tools.financials import get_financials_tool
        result = cast(FinancialsResult, await get_financials_tool.impl(ticker="AAPL", periods=4))
    # Wrapper returns a single pydantic model with a `periods` list
    assert result.ticker == "AAPL"
    assert len(result.periods) == 1
    assert result.periods[0].revenue == 391_035_000_000


@pytest.mark.asyncio
async def test_get_financials_tool_schema_shape() -> None:
    from app.tools.financials import get_financials_tool
    schema = get_financials_tool.schema
    assert schema["name"] == "get_financials"
    assert "ticker" in schema["input_schema"]["properties"]
    assert "periods" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["ticker"]


@pytest.mark.asyncio
async def test_get_key_ratios_returns_model() -> None:
    fake_ratios = KeyRatios(
        ticker="AAPL", as_of=date(2026, 5, 14),
        pe_ttm=29.5, pb=50.1, ps_ttm=8.8, roe=1.45,
        roic=0.30, fcf_yield=None, debt_to_equity=152.0,
        current_ratio=0.98, net_margin=0.255, revenue_growth_yoy=0.062,
    )
    with patch("app.tools.financials._fetch_key_ratios", new=AsyncMock(return_value=fake_ratios)):
        from app.tools.financials import get_key_ratios_tool
        result = cast(KeyRatios, await get_key_ratios_tool.impl(ticker="AAPL"))
    assert result.pe_ttm == 29.5
    assert result.net_margin == 0.255
