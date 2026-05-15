"use client";

import { useState } from "react";
import { resolveTicker, type TickerResolution } from "@/lib/api-resolve";

export function TickerAutocomplete({
  onPick,
  autoFocus,
}: {
  onPick: (r: TickerResolution) => void;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<TickerResolution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleResolve() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const r = await resolveTicker(query.trim());
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          autoFocus={autoFocus}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleResolve()}
          placeholder="Apple / RELIANCE.NS / BTC"
          className="border rounded-md px-2 py-1 flex-1 bg-background"
        />
        <button
          onClick={handleResolve}
          disabled={loading || !query.trim()}
          className="border rounded-md px-3 py-1 text-sm disabled:opacity-50"
        >
          {loading ? "..." : "Resolve"}
        </button>
      </div>
      {error && <div className="text-red-500 text-sm">{error}</div>}
      {result && (
        <div className="border rounded-md p-2 bg-muted">
          <div className="font-mono font-semibold">{result.ticker}</div>
          <div className="text-sm">
            {result.name} · {result.market} · {result.asset_class}
          </div>
          <div className="text-xs text-muted-foreground">
            confidence: {result.confidence.toFixed(2)}
          </div>
          <button
            onClick={() => onPick(result)}
            className="mt-1 border rounded-md px-3 py-1 bg-blue-500 text-white text-sm"
          >
            Use this ticker
          </button>
        </div>
      )}
    </div>
  );
}
