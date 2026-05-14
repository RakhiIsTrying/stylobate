export type DeltaText = { type: "text"; text: string };
export type DeltaBlock = DeltaText;

export type SSEEvent =
  | { event: "progress"; data: { step: string; message_id?: string } }
  | { event: "delta"; data: DeltaBlock }
  | { event: "done"; data: { message_id: string; chat_id: string } }
  | { event: "error"; data: { message: string } };

export type ChatMessageBlock = DeltaBlock;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: ChatMessageBlock[] | { type: "text"; text: string };
}
