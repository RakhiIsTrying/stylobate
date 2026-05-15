"use client";

import { useState } from "react";
import { createWatchlist, type Watchlist } from "@/lib/api-watchlists";

export function WatchlistSelector({
  watchlists,
  selectedId,
  onSelect,
  onCreate,
}: {
  watchlists: Watchlist[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (w: Watchlist) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!name.trim()) {
      setError("Name required");
      return;
    }
    try {
      const w = await createWatchlist(name.trim());
      onCreate(w);
      setCreating(false);
      setName("");
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {watchlists.length > 0 && (
        <select
          className="border rounded-md px-2 py-1 bg-background text-sm"
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value)}
        >
          {watchlists.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      )}
      {!creating && (
        <button
          onClick={() => setCreating(true)}
          className="border rounded-md px-3 py-1 text-sm hover:bg-muted"
        >
          + New
        </button>
      )}
      {creating && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Watchlist name"
            className="border rounded-md px-2 py-1 bg-background text-sm"
          />
          <button
            onClick={handleCreate}
            className="border rounded-md px-3 py-1 bg-blue-500 text-white text-sm hover:bg-blue-600"
          >
            Create
          </button>
          <button
            onClick={() => {
              setCreating(false);
              setError(null);
            }}
            className="text-muted-foreground text-sm hover:text-foreground"
          >
            Cancel
          </button>
          {error && <span className="text-red-500 text-sm">{error}</span>}
        </div>
      )}
    </div>
  );
}
