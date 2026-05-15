"use client";
import type { DeltaDisclaimer } from "@/lib/types";

export function Disclaimer({ delta }: { delta: DeltaDisclaimer }) {
  return (
    <p className="text-[11px] italic text-muted-foreground border-t pt-2">
      {delta.text}
    </p>
  );
}
