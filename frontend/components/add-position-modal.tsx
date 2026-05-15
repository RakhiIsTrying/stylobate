"use client";

import { useState } from "react";
import {
  addPosition,
  type AssetClass,
  type Market,
  type Position,
} from "@/lib/api-portfolios";
import { TickerAutocomplete } from "./ticker-autocomplete";

const CURRENCY_BY_MARKET: Record<Market, string> = {
  US: "USD",
  IN: "INR",
  CRYPTO: "USD",
};

export function AddPositionModal({
  portfolioId,
  onClose,
  onSaved,
}: {
  portfolioId: string;
  onClose: () => void;
  onSaved: (p: Position) => void;
}) {
  const [step, setStep] = useState<"pick" | "fill">("pick");
  const [resolved, setResolved] = useState<{
    ticker: string;
    market: Market;
    asset_class: AssetClass;
  } | null>(null);
  const [quantity, setQuantity] = useState("");
  const [costBasis, setCostBasis] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [openedAt, setOpenedAt] = useState<string>(
    new Date().toISOString().slice(0, 10),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    if (!resolved) return;
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
      const p = await addPosition(portfolioId, {
        ticker: resolved.ticker,
        market: resolved.market,
        asset_class: resolved.asset_class,
        quantity: qty,
        cost_basis: cost,
        currency: currency.toUpperCase(),
        opened_at: openedAt,
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
          <h2 className="text-lg font-semibold">Add position</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {step === "pick" && (
          <TickerAutocomplete
            autoFocus
            onPick={(r) => {
              setResolved({
                ticker: r.ticker,
                market: r.market,
                asset_class: r.asset_class,
              });
              setCurrency(CURRENCY_BY_MARKET[r.market]);
              setStep("fill");
            }}
          />
        )}

        {step === "fill" && resolved && (
          <>
            <div className="bg-muted rounded-md p-2 text-sm">
              <span className="font-mono font-semibold">{resolved.ticker}</span>{" "}
              · {resolved.market} · {resolved.asset_class}
              <button
                onClick={() => {
                  setResolved(null);
                  setStep("pick");
                }}
                className="text-xs text-blue-500 ml-2"
              >
                change
              </button>
            </div>

            <div>
              <label htmlFor="add-position-quantity" className="block text-sm">Quantity</label>
              <input
                id="add-position-quantity"
                type="number"
                step="any"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="border rounded-md px-2 py-1 w-full bg-background"
              />
            </div>
            <div>
              <label htmlFor="add-position-cost-basis" className="block text-sm">
                Cost basis (per unit, {currency})
              </label>
              <input
                id="add-position-cost-basis"
                type="number"
                step="any"
                min="0"
                value={costBasis}
                onChange={(e) => setCostBasis(e.target.value)}
                className="border rounded-md px-2 py-1 w-full bg-background"
              />
            </div>
            <details>
              <summary className="text-sm cursor-pointer text-muted-foreground">
                Override currency
              </summary>
              <input
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
                maxLength={3}
                className="border rounded-md px-2 py-1 w-24 mt-1 uppercase bg-background"
              />
            </details>
            <div>
              <label htmlFor="add-position-opened-at" className="block text-sm">Opened on</label>
              <input
                id="add-position-opened-at"
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
                {submitting ? "Saving..." : "Save position"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
