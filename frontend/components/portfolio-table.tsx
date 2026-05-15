"use client";

import type { Position, CohortSummary } from "@/lib/api-portfolios";

const currencySymbol: Record<string, string> = {
  USD: "$",
  INR: "₹",
  EUR: "€",
  GBP: "£",
};

function fmt(
  currency: string | null,
  value: number | string | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (Number.isNaN(num)) return "—";
  const sym = currency ? (currencySymbol[currency] ?? `${currency} `) : "";
  return `${sym}${num.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function pct(p: number | null): string {
  if (p === null) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${p.toFixed(1)}%`;
}

function cohortGroup(asset_class: string): "equity_etf" | "crypto" {
  return asset_class === "crypto" ? "crypto" : "equity_etf";
}

export function PortfolioTable({
  positions,
  cohorts,
  onEdit,
  onDelete,
}: {
  positions: Position[];
  cohorts: CohortSummary[];
  onEdit: (p: Position) => void;
  onDelete: (p: Position) => void;
}) {
  if (positions.length === 0) {
    return (
      <div className="border border-dashed rounded-md p-8 text-center text-muted-foreground">
        No positions yet. Click [+ Add position] to start.
      </div>
    );
  }

  const buckets = new Map<string, Position[]>();
  for (const p of positions) {
    const key = `${p.currency}:${cohortGroup(p.asset_class)}`;
    buckets.set(key, [...(buckets.get(key) ?? []), p]);
  }

  return (
    <div className="border rounded-md overflow-hidden">
      {cohorts.map((c) => {
        const key = `${c.currency}:${c.asset_class_group}`;
        const rows = buckets.get(key) ?? [];
        const label =
          c.asset_class_group === "crypto"
            ? `${c.currency} (Crypto)`
            : `${c.currency} (Equities & ETFs)`;
        const cohortPctClass =
          c.pl_pct === null
            ? "text-muted-foreground"
            : c.pl_pct >= 0
              ? "text-green-600"
              : "text-red-600";
        return (
          <div key={key}>
            <div className="bg-muted px-3 py-2 text-sm font-medium flex justify-between flex-wrap gap-2">
              <span>
                {label} — {c.positions_count}{" "}
                {c.positions_count === 1 ? "position" : "positions"}
              </span>
              <span>
                {fmt(c.currency, c.total_value_native)}{" "}
                <span className={cohortPctClass}>{pct(c.pl_pct)}</span>
              </span>
            </div>
            <table className="w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="text-left px-3 py-1">Ticker</th>
                  <th className="text-right px-3 py-1">Qty</th>
                  <th className="text-right px-3 py-1">Cost</th>
                  <th className="text-right px-3 py-1">Now</th>
                  <th className="text-right px-3 py-1">P/L</th>
                  <th className="px-3 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => {
                  const rowPctClass =
                    p.pl_pct === null
                      ? "text-muted-foreground"
                      : p.pl_pct >= 0
                        ? "text-green-600"
                        : "text-red-600";
                  return (
                    <tr key={p.id} className="border-t">
                      <td className="px-3 py-2 font-mono">{p.ticker}</td>
                      <td className="px-3 py-2 text-right">
                        {String(p.quantity)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {fmt(p.currency, p.cost_basis)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {fmt(p.currency, p.current_price)}
                      </td>
                      <td className={`px-3 py-2 text-right ${rowPctClass}`}>
                        {pct(p.pl_pct)}
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <button
                          onClick={() => onEdit(p)}
                          className="text-blue-500 mr-3 hover:underline"
                          aria-label="Edit"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => onDelete(p)}
                          className="text-red-500 hover:underline"
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
          </div>
        );
      })}
    </div>
  );
}
