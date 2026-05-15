from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db import portfolios as db
from app.models.portfolio import PortfolioIn, PortfolioListOut, PortfolioOut

router = APIRouter(tags=["portfolios"])


@router.get("/portfolios", response_model=PortfolioListOut)
async def list_endpoint(
    user: dict[str, Any] = Depends(get_current_user),
) -> PortfolioListOut:
    rows = await db.list_portfolios(user["sub"])
    return PortfolioListOut(portfolios=[PortfolioOut(**r) for r in rows])


@router.post("/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    req: PortfolioIn,
    user: dict[str, Any] = Depends(get_current_user),
) -> PortfolioOut:
    row = await db.create_portfolio(user["sub"], req.name, req.base_currency)
    return PortfolioOut(**row)


class PortfolioPatch(BaseModel):
    name: str | None = None
    base_currency: str | None = None


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def patch_endpoint(
    portfolio_id: UUID,
    req: PortfolioPatch,
    user: dict[str, Any] = Depends(get_current_user),
) -> PortfolioOut:
    row = await db.rename_portfolio(
        user["sub"], str(portfolio_id), req.name, req.base_currency,
    )
    if row is None:
        raise HTTPException(404, "Portfolio not found")
    return PortfolioOut(**row)


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    portfolio_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_portfolio(user["sub"], str(portfolio_id))
    if not ok:
        raise HTTPException(404, "Portfolio not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
