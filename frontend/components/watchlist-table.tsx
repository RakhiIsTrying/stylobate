"use client";

import { useState } from "react";
import type { WatchlistItem } from "@/lib/api-watchlists";
import type { PriceQuote } from "@/lib/api-prices";
import { deleteWatchlistItem } from "@/lib/api-watchlists";

const currencySymbol: Record<string, string> = {
  USD: "$",
  INR: "₹",
  EUR: "€",
  GBP: "£",
};

function fmt(currency: string | undefined, value: number | undefined): string {
  if (value === undefined) return "—";
  const sym = currency ? (currencySymbol[currency] ?? `${currency} `) : "";
  return `${sym}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function WatchlistTable({
  items,
  prices,
  watchlistId,
  onDeleted,
}: {
  items: WatchlistItem[];
  prices: Record<string, PriceQuote>;
  watchlistId: string;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  async function handleDelete(it: WatchlistItem) {
    if (!confirm(`Remove ${it.ticker}?`)) return;
    setBusy(`${it.ticker}:${it.market}`);
    try {
      await deleteWatchlistItem(watchlistId, it.ticker, it.market);
      onDeleted();
    } finally {
      setBusy(null);
    }
  }

  if (items.length === 0) {
    return (
      <div className="border border-dashed rounded-md p-8 text-center text-muted-foreground">
        No tickers yet. Click [+ Add ticker] to start.
      </div>
    );
  }

  return (
    <table className="w-full text-sm border rounded-md overflow-hidden">
      <thead className="text-xs text-muted-foreground bg-muted">
        <tr>
          <th className="text-left px-3 py-2">Ticker</th>
          <th className="text-left px-3 py-2">Market</th>
          <th className="text-right px-3 py-2">Price</th>
          <th className="text-left px-3 py-2">Notes</th>
          <th className="px-3 py-2"></th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const key = `${it.ticker}:${it.market}`;
          const price = prices[key];
          const disabled = busy === key;
          return (
            <tr key={key} className="border-t">
              <td className="px-3 py-2 font-mono">{it.ticker}</td>
              <td className="px-3 py-2">{it.market}</td>
              <td className="px-3 py-2 text-right">
                {fmt(price?.currency, price?.price)}
              </td>
              <td className="px-3 py-2 text-muted-foreground">
                {it.notes ?? ""}
              </td>
              <td className="px-3 py-2 text-right">
                <button
                  onClick={() => handleDelete(it)}
                  disabled={disabled}
                  className="text-red-500 disabled:opacity-50 text-sm hover:underline"
                  aria-label="Delete"
                >
                  Delete
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
