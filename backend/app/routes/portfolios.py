from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.data.prices_cache import get_prices
from app.db import portfolios as db
from app.models.portfolio import (
    CohortSummary,
    PortfolioIn,
    PortfolioListOut,
    PortfolioOut,
    PositionIn,
    PositionOut,
    PositionPatch,
    PositionsResponse,
    PositionWithPrice,
)

router = APIRouter(tags=["portfolios"])


def _cohort_group(asset_class: str) -> str:
    return "crypto" if asset_class == "crypto" else "equity_etf"


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


@router.get("/portfolios/{portfolio_id}/positions", response_model=PositionsResponse)
async def list_positions_endpoint(
    portfolio_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> PositionsResponse:
    if not await db.assert_portfolio_owned(user["sub"], str(portfolio_id)):
        raise HTTPException(404, "Portfolio not found")
    rows = await db.list_positions(user["sub"], str(portfolio_id))

    pairs = [(r["ticker"], r["market"]) for r in rows]
    prices = await get_prices(pairs)

    positions: list[PositionWithPrice] = []
    cohorts_acc: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "positions_count": 0,
            "total_cost_native": 0.0,
            "total_value_native": 0.0,
            "as_of": None,
            "any_missing": False,
        }
    )
    any_missing = False
    for r in rows:
        price = prices.get((r["ticker"], r["market"]))
        cost = float(r["cost_basis"]) if r["cost_basis"] is not None else 0.0
        qty = float(r["quantity"])
        cost_native = cost * qty
        value_native: float | None
        pl_pct: float | None
        if price is not None:
            value_native = price.price * qty
            pl_pct = ((price.price - cost) / cost * 100) if cost > 0 else None
        else:
            any_missing = True
            value_native = None
            pl_pct = None

        positions.append(
            PositionWithPrice(
                **r,
                current_price=price.price if price else None,
                price_currency=price.currency if price else None,
                as_of=price.as_of if price else None,
                pl_pct=pl_pct,
                value_native=value_native,
            )
        )

        key = (r["currency"], _cohort_group(r["asset_class"]))
        c = cohorts_acc[key]
        c["positions_count"] += 1
        c["total_cost_native"] += cost_native
        if value_native is not None and price is not None:
            if c["total_value_native"] is None:
                c["total_value_native"] = 0.0
            c["total_value_native"] += value_native
            prev_as_of: datetime | None = c["as_of"]
            c["as_of"] = price.as_of if prev_as_of is None else max(prev_as_of, price.as_of)
        else:
            c["any_missing"] = True

    cohorts_out: list[CohortSummary] = []
    for (currency, group), acc in cohorts_acc.items():
        tv: float | None = acc["total_value_native"] if not acc["any_missing"] else None
        cost_t = acc["total_cost_native"]
        pl = ((tv - cost_t) / cost_t * 100) if (tv is not None and cost_t > 0) else None
        cohorts_out.append(
            CohortSummary(
                currency=currency,
                asset_class_group=group,
                positions_count=acc["positions_count"],
                total_cost_native=cost_t,
                total_value_native=tv,
                pl_pct=pl,
                as_of=acc["as_of"],
            )
        )

    return PositionsResponse(
        portfolio_id=portfolio_id,
        positions=positions,
        cohorts=cohorts_out,
        prices_partial=any_missing,
    )
