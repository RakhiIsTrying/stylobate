# backend/tests/test_fred.py
from datetime import date

import pytest
import respx

_CSV_BODY = """DATE,DGS10
2026-05-12,4.41
2026-05-13,4.39
2026-05-14,4.42
"""

_CSV_WITH_MISSING = """DATE,DGS2
2026-05-12,.
2026-05-13,4.85
2026-05-14,4.87
"""


@pytest.mark.asyncio
async def test_fetch_fred_series_parses_csv() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "DGS10"}).respond(
            200, text=_CSV_BODY,
        )
        from app.data.fred import fetch_fred_series
        points = await fetch_fred_series("DGS10")
    assert len(points) == 3
    assert points[0].date == date(2026, 5, 12)
    assert points[0].value == 4.41
    assert points[2].value == 4.42


@pytest.mark.asyncio
async def test_fetch_fred_series_handles_missing_values() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "DGS2"}).respond(
            200, text=_CSV_WITH_MISSING,
        )
        from app.data.fred import fetch_fred_series
        points = await fetch_fred_series("DGS2")
    assert points[0].value is None
    assert points[1].value == 4.85


@pytest.mark.asyncio
async def test_fetch_fred_series_propagates_http_error() -> None:
    with respx.mock(base_url="https://fred.stlouisfed.org") as mock:
        mock.get("/graph/fredgraph.csv", params={"id": "BADID"}).respond(404, text="Not Found")
        from app.data.fred import FREDError, fetch_fred_series
        with pytest.raises(FREDError):
            await fetch_fred_series("BADID")
