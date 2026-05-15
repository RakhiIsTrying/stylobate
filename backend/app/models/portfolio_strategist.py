from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CohortGroup = Literal["equity_etf", "crypto"]


class PositionHint(BaseModel):
    ticker: str
    action: Literal["buy", "sell"]
    qty: float
    est_value_native: float


class RebalanceTrade(BaseModel):
    cohort: tuple[str, CohortGroup]
    action: Literal["increase", "decrease", "hold"]
    delta_native: float
    per_position_hints: list[PositionHint] = []


class Citation(BaseModel):
    source: str
    ref: str
