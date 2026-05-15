import { apiFetch } from "./api-base";
import type { Market } from "./api-portfolios";

export type Watchlist = {
  id: string;
  user_id: string;
  name: string;
  created_at: string;
};

export type WatchlistItem = {
  watchlist_id: string;
  ticker: string;
  market: Market;
  added_at: string;
  notes: string | null;
};

export async function listWatchlists(): Promise<Watchlist[]> {
  const r = await apiFetch<{ watchlists: Watchlist[] }>("/watchlists");
  return r.watchlists;
}

export async function createWatchlist(name: string): Promise<Watchlist> {
  return apiFetch<Watchlist>("/watchlists", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function deleteWatchlist(id: string): Promise<void> {
  await apiFetch(`/watchlists/${id}`, { method: "DELETE" });
}

export async function listWatchlistItems(
  wlId: string,
): Promise<WatchlistItem[]> {
  const r = await apiFetch<{ items: WatchlistItem[] }>(
    `/watchlists/${wlId}/items`,
  );
  return r.items;
}

export async function addWatchlistItem(
  wlId: string,
  ticker: string,
  market: Market,
  notes?: string,
): Promise<WatchlistItem> {
  return apiFetch<WatchlistItem>(`/watchlists/${wlId}/items`, {
    method: "POST",
    body: JSON.stringify({ ticker, market, notes }),
  });
}

export async function deleteWatchlistItem(
  wlId: string,
  ticker: string,
  market: Market,
): Promise<void> {
  await apiFetch(`/watchlists/${wlId}/items/${ticker}:${market}`, {
    method: "DELETE",
  });
}
