from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

CohortGroup = Literal["equity_etf", "crypto"]


class ConcentrationFlag(BaseModel):
    position_id: UUID | None = None
    ticker: str
    weight_pct: float
    threshold_pct: float = 0.10
    severity: Literal["warn", "critical"]


class CohortVaR(BaseModel):
    cohort: tuple[str, CohortGroup]
    confidence: float
    horizon_days: int
    var_pct: float | None = None
    var_native: float | None = None
    methodology: Literal["historical"] = "historical"
    insufficient_history: bool = False


class CorrelationMatrix(BaseModel):
    tickers: list[str]
    matrix: list[list[float]]
    excluded: list[str] = []


class PositionStressDelta(BaseModel):
    ticker: str
    before_native: float
    after_native: float
    delta_native: float
    delta_pct: float


class CohortStressDelta(BaseModel):
    cohort: tuple[str, CohortGroup]
    before_native: float
    after_native: float
    delta_native: float


class StressResult(BaseModel):
    scenario: Literal["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"]
    per_position: list[PositionStressDelta]
    by_cohort: list[CohortStressDelta]
    total_delta_usd: float
    assumed_fx: dict[str, float] = {}


class Citation(BaseModel):
    source: str
    ref: str
