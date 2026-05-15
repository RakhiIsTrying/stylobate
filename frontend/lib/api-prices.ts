import { apiFetch } from "./api-base";
import type { Market } from "./api-portfolios";

export type PriceQuote = {
  price: number;
  currency: string;
  as_of: string;
};

export type PricesResponse = {
  prices: Record<string, PriceQuote>;
  prices_partial: boolean;
};

export function tickerMarketKey(ticker: string, market: Market): string {
  return `${ticker}:${market}`;
}

export async function batchPrices(
  pairs: Array<[string, Market]>,
): Promise<PricesResponse> {
  const q = pairs.map(([t, m]) => `${t}:${m}`).join(",");
  return apiFetch<PricesResponse>(`/prices?tickers=${encodeURIComponent(q)}`);
}

export async function refreshPrices(
  pairs: Array<[string, Market]>,
): Promise<PricesResponse> {
  return apiFetch<PricesResponse>("/prices/refresh", {
    method: "POST",
    body: JSON.stringify({ tickers: pairs.map(([t, m]) => `${t}:${m}`) }),
  });
}
