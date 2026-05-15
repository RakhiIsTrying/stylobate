# backend/app/tools/financials.py
from pydantic import BaseModel

from app.data.yfinance_adapter import (
    Financials,
    KeyRatios,
)
from app.data.yfinance_adapter import (
    fetch_financials as _fetch_financials,
)
from app.data.yfinance_adapter import (
    fetch_key_ratios as _fetch_key_ratios,
)
from app.tools.base import Tool


class FinancialsResult(BaseModel):
    ticker: str
    periods: list[Financials]


async def _impl_get_financials(ticker: str, periods: int = 4) -> FinancialsResult:
    rows = await _fetch_financials(ticker=ticker, periods=periods)
    return FinancialsResult(ticker=ticker.upper(), periods=rows)


get_financials_tool = Tool(
    name="get_financials",
    description=(
        "Fetch income statement + cash flow for the last N periods (annual). "
        "Returns revenue, net income, operating cash flow, free cash flow."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
            "periods": {"type": "integer", "default": 4, "minimum": 1, "maximum": 10},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_financials,
)


async def _impl_get_key_ratios(ticker: str) -> KeyRatios:
    return await _fetch_key_ratios(ticker=ticker)


get_key_ratios_tool = Tool(
    name="get_key_ratios",
    description=(
        "Fetch valuation + profitability + leverage ratios for the ticker. "
        "Returns P/E, P/B, P/S, ROE, ROIC, FCF yield, D/E, current ratio, "
        "net margin, revenue growth YoY."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
        },
        "required": ["ticker"],
    },
    impl=_impl_get_key_ratios,
)
