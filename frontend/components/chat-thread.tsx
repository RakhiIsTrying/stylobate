"use client";
import type { ChatMessage } from "@/lib/types";

export function ChatThread({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex flex-col gap-4 p-6">
      {messages.map((m) => (
        <div key={m.id} className={m.role === "user" ? "self-end" : "self-start"}>
          <div className={
            "max-w-2xl rounded-lg px-4 py-2 text-sm " +
            (m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted")
          }>
            {Array.isArray(m.content)
              ? m.content.map((b, i) => (b.type === "text" ? <p key={i}>{b.text}</p> : null))
              : m.content.type === "text" ? <p>{m.content.text}</p> : null}
          </div>
        </div>
      ))}
    </div>
  );
}
