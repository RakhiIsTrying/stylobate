# backend/app/tools/filings.py
from pydantic import BaseModel

from app.data.edgar import FilingRef
from app.data.edgar import fetch_filings as _fetch_filings
from app.tools.base import Tool


class FilingsResult(BaseModel):
    ticker: str
    filings: list[FilingRef]


async def _impl_get_filings(
    ticker: str,
    types: list[str] | None = None,
    limit: int = 5,
) -> FilingsResult:
    refs = await _fetch_filings(ticker=ticker, types=types, limit=limit)
    return FilingsResult(ticker=ticker.upper(), filings=refs)


get_filings_tool = Tool(
    name="get_filings",
    description=(
        "List recent SEC filings for the ticker. types defaults to ['10-K', '10-Q', '8-K']. "
        "Returns at most `limit` filings with type, date, accession, and URL."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filing types to include. Default: 10-K, 10-Q, 8-K.",
            },
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_filings,
)
