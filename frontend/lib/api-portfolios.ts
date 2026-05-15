import { apiFetch } from "./api-base";

export type Market = "US" | "IN" | "CRYPTO";
export type AssetClass = "equity" | "etf" | "crypto";
export type CohortGroup = "equity_etf" | "crypto";

export type Portfolio = {
  id: string;
  user_id: string;
  name: string;
  base_currency: string;
  created_at: string;
  updated_at: string;
};

export type Position = {
  id: string;
  portfolio_id: string;
  ticker: string;
  market: Market;
  asset_class: AssetClass;
  quantity: number | string;
  cost_basis: number | string | null;
  currency: string;
  opened_at: string | null;
  created_at: string;
  current_price: number | null;
  price_currency: string | null;
  as_of: string | null;
  pl_pct: number | null;
  value_native: number | null;
};

export type CohortSummary = {
  currency: string;
  asset_class_group: CohortGroup;
  positions_count: number;
  total_cost_native: number;
  total_value_native: number | null;
  pl_pct: number | null;
  as_of: string | null;
};

export type PositionsResponse = {
  portfolio_id: string;
  positions: Position[];
  cohorts: CohortSummary[];
  prices_partial: boolean;
};

export type PositionInput = {
  ticker: string;
  market: Market;
  asset_class: AssetClass;
  quantity: number;
  cost_basis: number;
  currency: string;
  opened_at?: string;
};

export type PositionPatch = {
  quantity?: number;
  cost_basis?: number;
  opened_at?: string;
};

export async function listPortfolios(): Promise<Portfolio[]> {
  const r = await apiFetch<{ portfolios: Portfolio[] }>("/portfolios");
  return r.portfolios;
}

export async function createPortfolio(
  name: string,
  base_currency: string,
): Promise<Portfolio> {
  return apiFetch<Portfolio>("/portfolios", {
    method: "POST",
    body: JSON.stringify({ name, base_currency }),
  });
}

export async function deletePortfolio(id: string): Promise<void> {
  await apiFetch(`/portfolios/${id}`, { method: "DELETE" });
}

export async function listPositions(
  portfolioId: string,
): Promise<PositionsResponse> {
  return apiFetch<PositionsResponse>(`/portfolios/${portfolioId}/positions`);
}

export async function addPosition(
  portfolioId: string,
  p: PositionInput,
): Promise<Position> {
  return apiFetch<Position>(`/portfolios/${portfolioId}/positions`, {
    method: "POST",
    body: JSON.stringify(p),
  });
}

export async function patchPosition(
  positionId: string,
  patch: PositionPatch,
): Promise<Position> {
  return apiFetch<Position>(`/positions/${positionId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deletePosition(positionId: string): Promise<void> {
  await apiFetch(`/positions/${positionId}`, { method: "DELETE" });
}
