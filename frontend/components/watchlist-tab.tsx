"use client";

import { useCallback, useEffect, useState } from "react";
import {
  addWatchlistItem,
  listWatchlistItems,
  listWatchlists,
  type Watchlist,
  type WatchlistItem,
} from "@/lib/api-watchlists";
import { batchPrices, refreshPrices, type PriceQuote } from "@/lib/api-prices";
import type { Market } from "@/lib/api-portfolios";
import { RefreshButton } from "./refresh-button";
import { TickerAutocomplete } from "./ticker-autocomplete";
import { WatchlistSelector } from "./watchlist-selector";
import { WatchlistTable } from "./watchlist-table";

export function WatchlistTab() {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [prices, setPrices] = useState<Record<string, PriceQuote>>({});
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reloadItems = useCallback(async () => {
    if (!selectedId) return;
    try {
      const its = await listWatchlistItems(selectedId);
      setItems(its);
      if (its.length > 0) {
        const pairs = its.map(
          (i) => [i.ticker, i.market] as [string, Market],
        );
        const r = await batchPrices(pairs);
        setPrices(r.prices);
      } else {
        setPrices({});
      }
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selectedId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const wl = await listWatchlists();
        if (cancelled) return;
        setWatchlists(wl);
        setSelectedId((prev) => prev ?? (wl.length > 0 ? wl[0].id : null));
      } catch (e: unknown) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!selectedId) {
        await Promise.resolve();
        if (cancelled) return;
        setItems([]);
        setPrices({});
        setLoading(false);
        return;
      }
      try {
        const its = await listWatchlistItems(selectedId);
        if (cancelled) return;
        setItems(its);
        if (its.length > 0) {
          const pairs = its.map(
            (i) => [i.ticker, i.market] as [string, Market],
          );
          const r = await batchPrices(pairs);
          if (cancelled) return;
          setPrices(r.prices);
        } else {
          setPrices({});
        }
        setError(null);
      } catch (e: unknown) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function handleRefresh() {
    if (items.length === 0) return;
    const pairs = items.map((i) => [i.ticker, i.market] as [string, Market]);
    const r = await refreshPrices(pairs);
    setPrices(r.prices);
  }

  async function handleAdd(picked: { ticker: string; market: Market }) {
    if (!selectedId) return;
    try {
      await addWatchlistItem(
        selectedId,
        picked.ticker,
        picked.market,
        notes || undefined,
      );
      setNotes("");
      setAdding(false);
      await reloadItems();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (watchlists.length === 0 && !loading) {
    return (
      <div className="border border-dashed rounded-md p-8 text-center space-y-3">
        <p className="text-muted-foreground">No watchlists yet.</p>
        <div className="flex justify-center">
          <WatchlistSelector
            watchlists={[]}
            selectedId={null}
            onSelect={setSelectedId}
            onCreate={(w) => {
              setWatchlists([w]);
              setSelectedId(w.id);
            }}
          />
        </div>
        {error && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded-md text-sm">
            {error}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center flex-wrap gap-2">
        <WatchlistSelector
          watchlists={watchlists}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreate={(w) => {
            setWatchlists([...watchlists, w]);
            setSelectedId(w.id);
          }}
        />
        <div className="flex gap-2">
          <RefreshButton onRefresh={handleRefresh} />
          <button
            onClick={() => setAdding(!adding)}
            className="border rounded-md px-3 py-1 text-sm bg-blue-500 text-white hover:bg-blue-600"
          >
            + Add ticker
          </button>
        </div>
      </div>

      {adding && (
        <div className="border rounded-md p-3 space-y-2">
          <TickerAutocomplete autoFocus onPick={(r) => handleAdd(r)} />
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            className="border rounded-md px-2 py-1 w-full bg-background text-sm"
          />
        </div>
      )}

      {error && (
        <div className="bg-red-50 text-red-700 px-3 py-2 rounded-md text-sm">
          {error}
        </div>
      )}

      {selectedId && (
        <WatchlistTable
          items={items}
          prices={prices}
          watchlistId={selectedId}
          onDeleted={() => {
            void reloadItems();
          }}
        />
      )}
    </div>
  );
}
