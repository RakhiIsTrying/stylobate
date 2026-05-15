"use client";
import type { DeltaStockCard } from "@/lib/types";

export function StockCard({ delta }: { delta: DeltaStockCard }) {
  return (
    <div className="rounded-lg border bg-card p-4 flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="font-semibold text-lg">{delta.ticker}</div>
          <div className="text-xs text-muted-foreground">
            {delta.name} · {delta.market}
          </div>
        </div>
        <div className="text-xs text-muted-foreground">{delta.currency}</div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {Object.entries(delta.stats).map(([k, v]) => (
          <div key={k} className="flex flex-col">
            <span className="uppercase tracking-wider text-[10px] text-muted-foreground">{k}</span>
            <span className="font-medium">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
