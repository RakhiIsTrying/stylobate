"use client";

import { useState } from "react";
import type { Portfolio } from "@/lib/api-portfolios";
import { createPortfolio } from "@/lib/api-portfolios";

export function PortfolioSelector({
  portfolios,
  selectedId,
  onSelect,
  onCreate,
}: {
  portfolios: Portfolio[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (p: Portfolio) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCurrency, setNewCurrency] = useState("USD");
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!newName.trim()) {
      setError("Name required");
      return;
    }
    try {
      const p = await createPortfolio(
        newName.trim(),
        newCurrency.toUpperCase(),
      );
      onCreate(p);
      setCreating(false);
      setNewName("");
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create");
    }
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {portfolios.length > 0 && (
        <select
          className="border rounded-md px-2 py-1 bg-background text-sm"
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value)}
        >
          {portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.base_currency})
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
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Portfolio name"
            className="border rounded-md px-2 py-1 bg-background text-sm"
          />
          <input
            value={newCurrency}
            onChange={(e) => setNewCurrency(e.target.value)}
            placeholder="USD"
            maxLength={3}
            className="border rounded-md px-2 py-1 w-16 bg-background text-sm"
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
