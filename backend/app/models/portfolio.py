from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Market = Literal["US", "IN", "CRYPTO"]
AssetClass = Literal["equity", "etf", "crypto"]


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(min_length=3, max_length=3)

    @field_validator("base_currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("base_currency must be a 3-letter ISO code")
        return v


class PortfolioOut(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    base_currency: str
    created_at: datetime
    updated_at: datetime


class PortfolioListOut(BaseModel):
    portfolios: list[PortfolioOut]
