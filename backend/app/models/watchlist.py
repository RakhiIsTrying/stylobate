from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Market = Literal["US", "IN", "CRYPTO"]


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WatchlistOut(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    created_at: datetime


class WatchlistListOut(BaseModel):
    watchlists: list[WatchlistOut]


class WatchlistItemIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    market: Market
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper()


class WatchlistItemOut(BaseModel):
    watchlist_id: UUID
    ticker: str
    market: Market
    added_at: datetime
    notes: str | None


class WatchlistItemListOut(BaseModel):
    items: list[WatchlistItemOut]
