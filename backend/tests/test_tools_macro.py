# backend/tests/test_tools_macro.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.fred import TimeSeriesPoint


@pytest.mark.asyncio
async def test_get_rates_returns_typed_snapshot() -> None:
    fake_snap = {
        "fed_funds": 5.25,
        "treasury_2y": 4.87,
        "treasury_10y": 4.42,
        "real_10y": 2.10,
    }
    with patch("app.tools.macro._fetch_rates_snapshot", new=AsyncMock(return_value=fake_snap)):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl())
    assert r.fed_funds == 5.25
    assert r.treasury_10y == 4.42
    assert r.real_10y == 2.10


@pytest.mark.asyncio
async def test_get_sector_perf_returns_dict() -> None:
    fake_returns = {"XLK": 0.05, "XLF": 0.02, "XLV": -0.01}
    with patch(
        "app.tools.macro._fetch_sector_etf_returns",
        new=AsyncMock(return_value=fake_returns),
    ):
        from app.tools.macro import SectorPerfResult, get_sector_perf_tool
        r = cast(SectorPerfResult, await get_sector_perf_tool.impl(period="1mo"))
    assert r.period == "1mo"
    assert r.returns == fake_returns


@pytest.mark.asyncio
async def test_get_fred_series_returns_recent_points() -> None:
    fake_points = [
        TimeSeriesPoint(date=date(2026, 5, 1), value=4.40),
        TimeSeriesPoint(date=date(2026, 5, 14), value=4.42),
    ]
    with patch("app.tools.macro._fetch_fred_series", new=AsyncMock(return_value=fake_points)):
        from app.tools.macro import FREDSeriesResult, get_fred_series_tool
        r = cast(
            FREDSeriesResult,
            await get_fred_series_tool.impl(series_id="DGS10", lookback_days=30),
        )
    assert r.series_id == "DGS10"
    # Should include both points (both within 30 days of latest)
    assert len(r.points) == 2
    assert r.points[-1].value == 4.42


def test_macro_tools_schemas() -> None:
    from app.tools.macro import get_fred_series_tool, get_rates_tool, get_sector_perf_tool
    assert get_rates_tool.schema["name"] == "get_rates"
    assert get_sector_perf_tool.schema["name"] == "get_sector_perf"
    assert get_fred_series_tool.schema["name"] == "get_fred_series"
    # get_fred_series requires series_id
    assert "series_id" in get_fred_series_tool.schema["input_schema"]["required"]
