from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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


class PositionIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    market: Market
    asset_class: AssetClass
    quantity: Decimal = Field(gt=0)
    cost_basis: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    opened_at: date | None = None

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper()

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code")
        return v

    @model_validator(mode="after")
    def check_crypto_consistency(self) -> PositionIn:
        if self.market == "CRYPTO" and self.asset_class != "crypto":
            raise ValueError("market=CRYPTO requires asset_class=crypto")
        if self.asset_class == "crypto" and self.market != "CRYPTO":
            raise ValueError("asset_class=crypto requires market=CRYPTO")
        return self


class PositionPatch(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    cost_basis: Decimal | None = Field(default=None, ge=0)
    opened_at: date | None = None


class PositionOut(BaseModel):
    id: UUID
    portfolio_id: UUID
    ticker: str
    market: Market
    asset_class: AssetClass
    quantity: Decimal
    cost_basis: Decimal | None
    currency: str
    opened_at: date | None
    created_at: datetime
