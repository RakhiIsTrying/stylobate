from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Market = Literal["US", "IN", "CRYPTO"]
AssetClass = Literal["equity", "etf", "crypto"]


class Constituent(BaseModel):
    ticker: str
    name: str
    sector: str | None = None
    currency: str
    asset_class: AssetClass
    market: Market
    market_cap: float | None = None  # populated only for crypto


class Candidate(BaseModel):
    ticker: str
    name: str
    market: Market
    currency: str
    sector: str | None
    current_price: float | None
    market_cap: float | None
    pe_ttm: float | None
    roe_pct: float | None
    revenue_growth_yoy: float | None


class Citation(BaseModel):
    source: str
    ref: str


class ScreenerFindings(BaseModel):
    theme: str
    universes_used: list[str]
    candidates: list[Candidate]
    filters_applied: dict[str, Any]
    notes: list[str]
    citations: list[Citation]
    confidence: float
