"use client";

import type { CohortSummary } from "@/lib/api-portfolios";

const currencySymbol: Record<string, string> = {
  USD: "$",
  INR: "₹",
  EUR: "€",
  GBP: "£",
};

function fmt(currency: string, value: number | null): string {
  if (value === null) return "—";
  const sym = currencySymbol[currency] ?? `${currency} `;
  return `${sym}${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function pct(p: number | null): string {
  if (p === null) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${p.toFixed(1)}%`;
}

export function CohortCard({ c }: { c: CohortSummary }) {
  const label =
    c.asset_class_group === "crypto"
      ? `${c.currency} (Crypto)`
      : `${c.currency} (Equities & ETFs)`;
  const pctClass =
    c.pl_pct === null
      ? "text-muted-foreground"
      : c.pl_pct >= 0
        ? "text-green-600"
        : "text-red-600";
  return (
    <div className="border rounded-md p-3 min-w-[160px] bg-card">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-lg font-semibold">
        {fmt(c.currency, c.total_value_native)}
      </div>
      <div className={`text-sm ${pctClass}`}>{pct(c.pl_pct)}</div>
      <div className="text-xs text-muted-foreground">
        {c.positions_count} {c.positions_count === 1 ? "position" : "positions"}
      </div>
    </div>
  );
}
