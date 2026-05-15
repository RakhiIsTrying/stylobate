"use client";
import type { Citation } from "@/lib/types";

export function CitationBadge({ citation }: { citation: Citation }) {
  const label = citation.index
    ? `${citation.source}·${citation.index}`
    : citation.source;
  return (
    <span
      title={citation.ref}
      className="inline-block rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-foreground/70 mx-0.5"
    >
      {label}
    </span>
  );
}
