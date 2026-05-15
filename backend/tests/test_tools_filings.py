# backend/tests/test_tools_filings.py
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.data.edgar import FilingRef
from app.tools.filings import FilingsResult


@pytest.mark.asyncio
async def test_get_filings_returns_typed_list() -> None:
    fake = [
        FilingRef(type="10-K", date=date(2024, 11, 1),
                  url="https://www.sec.gov/Archives/edgar/data/320193/aapl-10k.htm",
                  accession="0000320193-24-000123"),
        FilingRef(type="10-Q", date=date(2024, 8, 2),
                  url="https://www.sec.gov/Archives/edgar/data/320193/aapl-10q.htm",
                  accession="0000320193-24-000099"),
    ]
    with patch("app.tools.filings._fetch_filings", new=AsyncMock(return_value=fake)):
        from app.tools.filings import get_filings_tool
        result = cast(
            FilingsResult,
            await get_filings_tool.impl(ticker="AAPL", types=["10-K", "10-Q"], limit=5),
        )
    assert result.ticker == "AAPL"
    assert len(result.filings) == 2
    assert result.filings[0].type == "10-K"


def test_get_filings_tool_schema_shape() -> None:
    from app.tools.filings import get_filings_tool
    s = get_filings_tool.schema
    assert s["name"] == "get_filings"
    assert "ticker" in s["input_schema"]["properties"]
    assert "types" in s["input_schema"]["properties"]
    assert s["input_schema"]["required"] == ["ticker"]
