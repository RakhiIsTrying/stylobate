# backend/app/tools/macro.py
from datetime import date, timedelta

from pydantic import BaseModel

from app.data.fred import (
    TimeSeriesPoint,
)
from app.data.fred import (
    fetch_fred_series as _fetch_fred_series,
)
from app.data.fred import (
    fetch_rates_snapshot as _fetch_rates_snapshot,
)
from app.data.yfinance_adapter import (
    fetch_sector_etf_returns as _fetch_sector_etf_returns,
)
from app.tools.base import Tool


# ============================================================================
# get_rates
# ============================================================================
class RatesResult(BaseModel):
    fed_funds: float | None
    treasury_2y: float | None
    treasury_10y: float | None
    real_10y: float | None


async def _impl_get_rates() -> RatesResult:
    snap = await _fetch_rates_snapshot()
    return RatesResult(
        fed_funds=snap.get("fed_funds"),
        treasury_2y=snap.get("treasury_2y"),
        treasury_10y=snap.get("treasury_10y"),
        real_10y=snap.get("real_10y"),
    )


get_rates_tool = Tool(
    name="get_rates",
    description=(
        "Latest US policy + Treasury rates from FRED: fed funds, 2Y, 10Y, "
        "10Y real (TIPS). Returns null for any series temporarily unavailable."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    impl=_impl_get_rates,
)


# ============================================================================
# get_sector_perf
# ============================================================================
class SectorPerfResult(BaseModel):
    period: str
    returns: dict[str, float]  # ETF ticker → period return


async def _impl_get_sector_perf(period: str = "1mo") -> SectorPerfResult:
    returns = await _fetch_sector_etf_returns(period=period)
    return SectorPerfResult(period=period, returns=returns)


get_sector_perf_tool = Tool(
    name="get_sector_perf",
    description=(
        "Period returns for the 11 SPDR sector ETFs (XLK tech, XLF financials, "
        "XLV healthcare, XLE energy, XLY consumer disc, XLP staples, XLI industrials, "
        "XLB materials, XLU utilities, XLRE real estate, XLC communications). "
        "period: 1mo / 3mo / 6mo / 1y / ytd."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "period": {"type": "string", "default": "1mo"},
        },
        "required": [],
    },
    impl=_impl_get_sector_perf,
)


# ============================================================================
# get_fred_series — arbitrary FRED series
# ============================================================================
class FREDSeriesResult(BaseModel):
    series_id: str
    points: list[TimeSeriesPoint]


async def _impl_get_fred_series(
    series_id: str,
    lookback_days: int = 365,
) -> FREDSeriesResult:
    all_points = await _fetch_fred_series(series_id)
    if not all_points:
        return FREDSeriesResult(series_id=series_id, points=[])
    cutoff = date.today() - timedelta(days=lookback_days)
    recent = [p for p in all_points if p.date >= cutoff]
    return FREDSeriesResult(series_id=series_id, points=recent)


get_fred_series_tool = Tool(
    name="get_fred_series",
    description=(
        "Fetch any FRED time series by id (e.g. CPIAUCSL for CPI, GDP for GDP, "
        "UNRATE for unemployment). Returns the last `lookback_days` of data."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": "FRED series identifier, e.g. CPIAUCSL",
            },
            "lookback_days": {
                "type": "integer",
                "default": 365,
                "minimum": 30,
                "maximum": 3650,
            },
        },
        "required": ["series_id"],
    },
    impl=_impl_get_fred_series,
)
