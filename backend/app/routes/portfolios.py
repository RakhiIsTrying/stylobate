from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db import portfolios as db
from app.models.portfolio import (
    PortfolioIn,
    PortfolioListOut,
    PortfolioOut,
    PositionIn,
    PositionOut,
    PositionPatch,
)

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


@router.post("/portfolios/{portfolio_id}/positions", response_model=PositionOut)
async def add_position(
    portfolio_id: UUID,
    req: PositionIn,
    response: Response,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionOut:
    if not await db.assert_portfolio_owned(user["sub"], str(portfolio_id)):
        raise HTTPException(404, "Portfolio not found")
    existing = await db.get_position_by_ticker(
        str(portfolio_id), req.ticker, req.market,
    )
    if existing is None:
        row = await db.insert_position(
            str(portfolio_id),
            req.ticker,
            req.market,
            req.asset_class,
            req.quantity,
            req.cost_basis,
            req.currency,
            req.opened_at,
        )
        response.status_code = status.HTTP_201_CREATED
        return PositionOut(**row)

    # UPSERT path: weighted-average cost basis, sum of quantities, earliest opened_at
    old_qty = Decimal(str(existing["quantity"]))
    old_basis = Decimal(str(existing["cost_basis"] or 0))
    new_qty = old_qty + req.quantity
    new_basis = (old_qty * old_basis + req.quantity * req.cost_basis) / new_qty
    existing_opened = existing.get("opened_at")
    if existing_opened is not None and req.opened_at is not None:
        earliest = min(existing_opened, req.opened_at)
    elif existing_opened is not None:
        earliest = existing_opened
    else:
        earliest = req.opened_at
    updated = await db.update_position(
        str(existing["id"]),
        user["sub"],
        quantity=new_qty,
        cost_basis=new_basis,
        opened_at=earliest,
    )
    if updated is None:
        raise HTTPException(404, "Position not found")
    return PositionOut(**updated)


@router.patch("/positions/{position_id}", response_model=PositionOut)
async def patch_position(
    position_id: UUID,
    req: PositionPatch,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionOut:
    row = await db.update_position(
        str(position_id),
        user["sub"],
        quantity=req.quantity,
        cost_basis=req.cost_basis,
        opened_at=req.opened_at,
    )
    if row is None:
        raise HTTPException(404, "Position not found")
    return PositionOut(**row)


@router.delete("/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_position_endpoint(
    position_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    ok = await db.delete_position(str(position_id), user["sub"])
    if not ok:
        raise HTTPException(404, "Position not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
