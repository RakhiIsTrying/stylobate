"use client";
import type { DeltaRecommendation } from "@/lib/types";

const SIGNAL_LABEL: Record<DeltaRecommendation["signal"], string> = {
  tactical_buy: "Tactical Buy",
  accumulate: "Accumulate",
  hold: "Hold",
  reduce: "Reduce",
};

export function RecommendationCard({ delta }: { delta: DeltaRecommendation }) {
  const [low, high] = delta.position_size_range;
  return (
    <div className="rounded-lg border-2 border-amber-500/50 bg-amber-500/5 p-4 flex flex-col gap-2">
      <div className="text-sm font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
        {SIGNAL_LABEL[delta.signal]}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <Stat label="Position" value={`${low}–${high}%`} />
        <Stat label="Entry" value={delta.entry_zone} />
        <Stat label="Stop" value={delta.stop} />
        <Stat label="12-mo target" value={delta.target_12mo_base} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="uppercase tracking-wider text-[10px] text-muted-foreground">
        {label}
      </span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}
