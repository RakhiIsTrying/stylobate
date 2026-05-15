"use client";
import { CitationBadge } from "./citation-badge";
import { Disclaimer } from "./disclaimer";
import { ProgressStrip } from "./progress-strip";
import { RecommendationCard } from "./recommendation-card";
import { SectionBlock } from "./section-block";
import { StockCard } from "./stock-card";
import type { ChatMessage, ChatMessageBlock, DeltaQuickTake } from "@/lib/types";

const SIGNAL_BG: Record<DeltaQuickTake["signal"], string> = {
  tactical_buy: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  accumulate: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  hold: "bg-muted",
  reduce: "bg-rose-500/10 text-rose-700 dark:text-rose-400",
};

function renderBlock(block: ChatMessageBlock, i: number): React.ReactNode {
  switch (block.type) {
    case "text":
      return <p key={i}>{block.text}</p>;
    case "quick_take":
      return (
        <div key={i} className={`rounded px-3 py-2 text-sm font-medium ${SIGNAL_BG[block.signal]}`}>
          {block.signal.replace("_", " ")} · {block.qualifier}
        </div>
      );
    case "stock_card":
      return <StockCard key={i} delta={block} />;
    case "section":
      return <SectionBlock key={i} delta={block} />;
    case "recommendation":
      return <RecommendationCard key={i} delta={block} />;
    case "disclaimer":
      return <Disclaimer key={i} delta={block} />;
    default:
      return null;
  }
}

export function ChatThread({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex flex-col gap-6 p-6">
      {messages.map((m) => (
        <div
          key={m.id}
          className={m.role === "user" ? "self-end max-w-2xl" : "self-stretch max-w-3xl"}
        >
          {m.role === "user" ? (
            <div className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
              {Array.isArray(m.content) ? null
                : m.content.type === "text" ? <p>{m.content.text}</p>
                : null}
            </div>
          ) : (
            <div className="flex flex-col gap-4 rounded-lg bg-muted/30 px-4 py-3">
              {m.progress && m.progress.length > 0 && <ProgressStrip steps={m.progress} />}
              {Array.isArray(m.content)
                ? m.content.map((b, i) => renderBlock(b, i))
                : m.content.type === "text"
                  ? <p>{m.content.text}</p>
                  : null}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// re-exported so tests can import the badge from the thread module path too
export { CitationBadge };
