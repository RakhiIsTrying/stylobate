# backend/app/data/fred.py
"""FRED time series via the public CSV endpoint (no API key required).

`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series_id>` returns CSV
with a DATE column and the series id as the second column. Missing values
are represented as the literal '.'.
"""
import csv
from datetime import date
from io import StringIO

import httpx
from pydantic import BaseModel

_BASE = "https://fred.stlouisfed.org"
_USER_AGENT = "Stylobate research-bot (contact: rakhisinha100896@gmail.com)"


class FREDError(Exception):
    pass


class TimeSeriesPoint(BaseModel):
    date: date
    value: float | None


async def fetch_fred_series(series_id: str) -> list[TimeSeriesPoint]:
    """Fetch a FRED series via the public CSV endpoint. Returns oldest-first."""
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=10.0) as c:
        resp = await c.get(f"{_BASE}/graph/fredgraph.csv", params={"id": series_id})
    if resp.status_code != 200:
        raise FREDError(f"FRED returned {resp.status_code} for series {series_id}")

    out: list[TimeSeriesPoint] = []
    reader = csv.reader(StringIO(resp.text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise FREDError(f"FRED returned no usable header for {series_id}")
    for row in reader:
        if len(row) < 2:
            continue
        d_str, v_str = row[0], row[1]
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if v_str == "." or v_str == "":
            out.append(TimeSeriesPoint(date=d, value=None))
        else:
            try:
                out.append(TimeSeriesPoint(date=d, value=float(v_str)))
            except ValueError:
                out.append(TimeSeriesPoint(date=d, value=None))
    return out


def _last_value(points: list[TimeSeriesPoint]) -> float | None:
    """Last non-null value in the series."""
    for p in reversed(points):
        if p.value is not None:
            return p.value
    return None


async def fetch_rates_snapshot() -> dict[str, float | None]:
    """Latest values of the key US rates.

    Series IDs:
    - FEDFUNDS: Effective Fed Funds Rate
    - DGS2: 2-Year Treasury Constant Maturity Rate
    - DGS10: 10-Year Treasury Constant Maturity Rate
    - DFII10: 10-Year TIPS (real yield)
    """
    series = {
        "fed_funds": "FEDFUNDS",
        "treasury_2y": "DGS2",
        "treasury_10y": "DGS10",
        "real_10y": "DFII10",
    }
    out: dict[str, float | None] = {}
    for key, series_id in series.items():
        try:
            points = await fetch_fred_series(series_id)
            out[key] = _last_value(points)
        except FREDError:
            out[key] = None
    return out
