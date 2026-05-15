"use client";

const LABELS: Record<string, string> = {
  resolving_ticker: "Resolving ticker",
  running_specialists: "Consulting specialists",
  running_fundamentals: "Running Fundamental Analyst", // legacy, kept for older DB rows
  synthesizing: "Synthesizing",
  received: "Received",
};

export function ProgressStrip({ steps }: { steps: { step: string; ticker?: string }[] }) {
  if (steps.length === 0) return null;
  const last = steps[steps.length - 1];
  return (
    <div className="text-xs text-muted-foreground italic">
      {LABELS[last.step] ?? last.step}
      {last.ticker ? ` · ${last.ticker}` : ""}…
    </div>
  );
}
