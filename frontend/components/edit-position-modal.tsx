"use client";

import { useState } from "react";
import { patchPosition, type Position } from "@/lib/api-portfolios";

export function EditPositionModal({
  position,
  onClose,
  onSaved,
}: {
  position: Position;
  onClose: () => void;
  onSaved: (p: Position) => void;
}) {
  const [quantity, setQuantity] = useState(String(position.quantity));
  const [costBasis, setCostBasis] = useState(
    position.cost_basis === null ? "" : String(position.cost_basis),
  );
  const [openedAt, setOpenedAt] = useState(position.opened_at ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    const qty = parseFloat(quantity);
    const cost = parseFloat(costBasis);
    if (!Number.isFinite(qty) || qty <= 0) {
      setError("Quantity must be > 0");
      return;
    }
    if (!Number.isFinite(cost) || cost < 0) {
      setError("Cost basis must be ≥ 0");
      return;
    }
    setSubmitting(true);
    try {
      const p = await patchPosition(position.id, {
        quantity: qty,
        cost_basis: cost,
        opened_at: openedAt || undefined,
      });
      onSaved(p);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-card border rounded-md p-6 w-full max-w-md space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">Edit {position.ticker}</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div>
          <label className="block text-sm">Quantity</label>
          <input
            type="number"
            step="any"
            min="0"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className="border rounded-md px-2 py-1 w-full bg-background"
          />
        </div>
        <div>
          <label className="block text-sm">
            Cost basis (per unit, {position.currency})
          </label>
          <input
            type="number"
            step="any"
            min="0"
            value={costBasis}
            onChange={(e) => setCostBasis(e.target.value)}
            className="border rounded-md px-2 py-1 w-full bg-background"
          />
        </div>
        <div>
          <label className="block text-sm">Opened on</label>
          <input
            type="date"
            value={openedAt}
            onChange={(e) => setOpenedAt(e.target.value)}
            className="border rounded-md px-2 py-1 bg-background"
          />
        </div>
        {error && <div className="text-red-500 text-sm">{error}</div>}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="border rounded-md px-3 py-1 text-sm"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={submitting}
            className="border rounded-md px-3 py-1 bg-blue-500 text-white text-sm disabled:opacity-50"
          >
            {submitting ? "Saving..." : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
