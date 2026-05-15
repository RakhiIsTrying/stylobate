export type Citation = { source: string; ref: string; index?: number };

export type DeltaQuickTake = {
  type: "quick_take";
  signal: "tactical_buy" | "accumulate" | "hold" | "reduce";
  qualifier: string;
};

export type DeltaStockCard = {
  type: "stock_card";
  ticker: string;
  name: string;
  market: string;
  currency: string;
  stats: Record<string, string>;
};

export type DeltaSection = {
  type: "section";
  title: string;
  markdown: string;
  citations: Citation[];
};

export type DeltaRecommendation = {
  type: "recommendation";
  signal: "tactical_buy" | "accumulate" | "hold" | "reduce";
  position_size_range: [number, number];
  entry_zone: string;
  stop: string;
  target_12mo_base: string;
};

export type DeltaDisclaimer = { type: "disclaimer"; text: string };
export type DeltaText = { type: "text"; text: string };

export type DeltaBlock =
  | DeltaQuickTake
  | DeltaStockCard
  | DeltaSection
  | DeltaRecommendation
  | DeltaDisclaimer
  | DeltaText;

export type SSEEvent =
  | { event: "progress"; data: { step: string; ticker?: string; message_id?: string } }
  | { event: "delta"; data: DeltaBlock }
  | { event: "done"; data: { message_id: string | null; chat_id: string } }
  | { event: "error"; data: { message: string } };

export type ChatMessageBlock = DeltaBlock;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: ChatMessageBlock[] | { type: "text"; text: string };
  progress?: { step: string; ticker?: string }[];
}
