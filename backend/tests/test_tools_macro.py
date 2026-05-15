# backend/tests/test_tools_macro.py
from datetime import date
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.fred import TimeSeriesPoint


@pytest.mark.asyncio
async def test_get_rates_returns_typed_snapshot() -> None:
    """Default market=US returns all four US rate fields populated, mapped correctly."""
    per_series: dict[str, float] = {
        "FEDFUNDS": 5.25,
        "DGS2": 4.80,
        "DGS10": 4.42,
        "DFII10": 2.10,
    }

    async def fake_fred(series_id: str) -> list[Any]:
        from datetime import date

        from app.data.fred import TimeSeriesPoint
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=per_series[series_id])]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl())
    assert r.market == "US"
    assert r.fed_funds == 5.25
    assert r.treasury_2y == 4.80
    assert r.treasury_10y == 4.42
    assert r.real_10y == 2.10
    # Generic-field aliases for US: policy_rate echoes fed_funds, long_yield echoes treasury_10y
    assert r.policy_rate == 5.25
    assert r.long_yield == 4.42


@pytest.mark.asyncio
async def test_get_rates_unknown_market_raises() -> None:
    from app.tools.macro import get_rates_tool
    with pytest.raises(ValueError, match="unknown market"):
        await get_rates_tool.impl(market="EU")


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


@pytest.mark.asyncio
async def test_get_rates_us_market_returns_us_series() -> None:
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from datetime import date

        from app.data.fred import TimeSeriesPoint
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=4.42)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl(market="US"))
    # Should pull FEDFUNDS, DGS2, DGS10, DFII10
    assert "FEDFUNDS" in captured
    assert "DGS10" in captured
    assert r.policy_rate == 4.42
    # Legacy field for US callers
    assert r.fed_funds == 4.42


@pytest.mark.asyncio
async def test_get_rates_indian_market_returns_in_series() -> None:
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from datetime import date

        from app.data.fred import TimeSeriesPoint
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=6.50)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl(market="IN"))
    # Should pull the Indian series, NOT FEDFUNDS
    assert "INDIRSTPRLR01STM" in captured
    assert "IRLTLT01INM156N" in captured
    assert "FEDFUNDS" not in captured
    assert r.policy_rate == 6.50
    # US legacy field should be None when market is IN
    assert r.fed_funds is None


@pytest.mark.asyncio
async def test_get_rates_crypto_uses_us_series() -> None:
    """Crypto trades against USD, so US macro is the right context."""
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from datetime import date

        from app.data.fred import TimeSeriesPoint
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=4.42)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import get_rates_tool
        await get_rates_tool.impl(market="CRYPTO")
    assert "FEDFUNDS" in captured


def test_get_rates_schema_documents_market() -> None:
    from app.tools.macro import get_rates_tool
    s = get_rates_tool.schema
    assert "market" in s["input_schema"]["properties"]
    market_enum = s["input_schema"]["properties"]["market"]["enum"]
    assert set(market_enum) == {"US", "IN", "CRYPTO"}
