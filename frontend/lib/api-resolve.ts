import { apiFetch } from "./api-base";

export type TickerResolution = {
  ticker: string;
  name: string;
  market: "US" | "IN" | "CRYPTO";
  asset_class: "equity" | "etf" | "crypto";
  confidence: number;
};

export async function resolveTicker(query: string): Promise<TickerResolution> {
  return apiFetch<TickerResolution>("/resolve", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}
