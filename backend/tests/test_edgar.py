# backend/tests/test_edgar.py

import pytest
import respx
from httpx import Response

TICKER_LOOKUP_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
}


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    from app.data.edgar import _clear_caches
    _clear_caches()


@pytest.mark.asyncio
async def test_resolve_cik_for_known_ticker() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import resolve_cik
        cik = await resolve_cik("AAPL")
    assert cik == "0000320193"


@pytest.mark.asyncio
async def test_resolve_cik_unknown_returns_none() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import resolve_cik
        assert await resolve_cik("NOPE") is None


@pytest.mark.asyncio
async def test_fetch_filings_returns_typed_refs() -> None:
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-Q", "8-K", "10-Q"],
                "filingDate": ["2024-11-01", "2024-08-02", "2024-07-30", "2024-05-03"],
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000099",
                    "0000320193-24-000088",
                    "0000320193-24-000060",
                ],
                "primaryDocument": [
                    "aapl-20240928.htm",
                    "aapl-20240629.htm",
                    "aapl-8k.htm",
                    "aapl-20240330.htm",
                ],
            }
        }
    }

    with respx.mock(base_url="https://www.sec.gov") as mock_sec, \
         respx.mock(base_url="https://data.sec.gov") as mock_data:
        mock_sec.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        mock_data.get("/submissions/CIK0000320193.json").mock(
            return_value=Response(200, json=submissions)
        )
        from app.data.edgar import fetch_filings
        refs = await fetch_filings("AAPL", types=["10-K", "10-Q"], limit=5)

    assert len(refs) == 3
    assert refs[0].type == "10-K"
    assert refs[0].accession == "0000320193-24-000123"
    assert refs[0].url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert refs[1].type == "10-Q"
    assert refs[2].type == "10-Q"


@pytest.mark.asyncio
async def test_fetch_filings_unknown_ticker_returns_empty() -> None:
    with respx.mock(base_url="https://www.sec.gov") as mock:
        mock.get("/files/company_tickers.json").mock(
            return_value=Response(200, json=TICKER_LOOKUP_PAYLOAD)
        )
        from app.data.edgar import fetch_filings
        refs = await fetch_filings("NOPE")
    assert refs == []
