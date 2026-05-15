# backend/app/tools/macro.py
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from app.data.fred import (
    TimeSeriesPoint,
)
from app.data.fred import (
    fetch_fred_series as _fetch_fred_series,
)
from app.data.yfinance_adapter import (
    fetch_sector_etf_returns as _fetch_sector_etf_returns,
)
from app.tools.base import Tool


# ============================================================================
# get_rates
# ============================================================================
class RatesResult(BaseModel):
    market: Literal["US", "IN", "CRYPTO"]
    # Generic fields populated for any market
    policy_rate: float | None = None       # FedFunds / Repo Rate / FedFunds (crypto)
    long_yield: float | None = None        # 10Y Treasury / 10Y G-Sec / 10Y Treasury
    inflation: float | None = None         # CPI YoY (US/IN); None for crypto
    # US-specific legacy fields (populated when market == "US"; None otherwise)
    fed_funds: float | None = None
    treasury_2y: float | None = None
    treasury_10y: float | None = None
    real_10y: float | None = None


_US_RATE_SERIES: dict[str, str] = {
    "fed_funds": "FEDFUNDS",
    "treasury_2y": "DGS2",
    "treasury_10y": "DGS10",
    "real_10y": "DFII10",
}

_IN_RATE_SERIES: dict[str, str] = {
    "policy_rate": "INDIRSTPRLR01STM",  # India - Repo Rate
    "long_yield": "IRLTLT01INM156N",    # India - 10Y Government Bond Yield
    "inflation": "INDCPIALLMINMEI",     # India - CPI All Items
}


def _last_value(points: list[TimeSeriesPoint]) -> float | None:
    for p in reversed(points):
        if p.value is not None:
            return p.value
    return None


async def _impl_get_rates(market: str = "US") -> RatesResult:
    mkt = market if market in ("US", "IN", "CRYPTO") else "US"
    if mkt == "IN":
        values: dict[str, float | None] = {}
        for key, series_id in _IN_RATE_SERIES.items():
            try:
                pts = await _fetch_fred_series(series_id)
                values[key] = _last_value(pts)
            except Exception:
                values[key] = None
        return RatesResult(
            market="IN",
            policy_rate=values.get("policy_rate"),
            long_yield=values.get("long_yield"),
            inflation=values.get("inflation"),
        )

    # US or CRYPTO -> US series (crypto trades against USD)
    values_us: dict[str, float | None] = {}
    for key, series_id in _US_RATE_SERIES.items():
        try:
            pts = await _fetch_fred_series(series_id)
            values_us[key] = _last_value(pts)
        except Exception:
            values_us[key] = None
    return RatesResult(
        market=mkt,  # "US" or "CRYPTO"
        policy_rate=values_us["fed_funds"],
        long_yield=values_us["treasury_10y"],
        fed_funds=values_us["fed_funds"],
        treasury_2y=values_us["treasury_2y"],
        treasury_10y=values_us["treasury_10y"],
        real_10y=values_us["real_10y"],
    )


get_rates_tool = Tool(
    name="get_rates",
    description=(
        "Latest policy + long-rate snapshot. market='US' returns Fed Funds, 2Y, "
        "10Y, real 10Y. market='IN' returns Repo Rate, 10Y G-Sec, CPI. "
        "market='CRYPTO' returns US rates (crypto trades against USD). "
        "Defaults to US."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
        },
        "required": [],
    },
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
