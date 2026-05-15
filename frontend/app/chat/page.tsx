"use client";
import { useEffect, useState } from "react";

import { ChatThread } from "@/components/chat-thread";
import { Composer } from "@/components/composer";
import { parseSSEStream, postChatStream } from "@/lib/sse";
import { createClient } from "@/lib/supabase/client";
import type { ChatMessage, ChatMessageBlock } from "@/lib/types";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [chatId, setChatId] = useState<string | null>(null);
  const [jwt, setJwt] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      setJwt(session?.access_token ?? null);
    });
  }, []);

  async function handleSend(text: string) {
    if (!jwt) return;
    setSending(true);
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: { type: "text", text },
    };
    setMessages((m) => [...m, userMsg]);

    try {
      const stream = await postChatStream({
        jwt, content: text, chatId, backendUrl: BACKEND_URL,
      });
      const asstId = crypto.randomUUID();
      const asstMsg: ChatMessage = {
        id: asstId, role: "assistant", content: [], progress: [],
      };
      setMessages((m) => [...m, asstMsg]);
      const blocks: ChatMessageBlock[] = [];
      const progress: { step: string; ticker?: string }[] = [];

      for await (const ev of parseSSEStream(stream)) {
        if (ev.event === "progress") {
          progress.push({ step: ev.data.step, ticker: ev.data.ticker });
          const progSnapshot = [...progress];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, progress: progSnapshot } : x),
          );
        } else if (ev.event === "delta") {
          blocks.push(ev.data);
          const blocksSnapshot = [...blocks];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, content: blocksSnapshot } : x),
          );
        } else if (ev.event === "done") {
          if (ev.data.chat_id) setChatId(ev.data.chat_id);
        } else if (ev.event === "error") {
          blocks.push({ type: "text", text: `Error: ${ev.data.message}` });
          const blocksSnapshot = [...blocks];
          setMessages((m) =>
            m.map((x) => x.id === asstId ? { ...x, content: blocksSnapshot } : x),
          );
        }
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h1 className="text-sm font-semibold">Stylobate</h1>
      </header>
      <div className="flex-1 overflow-y-auto">
        <ChatThread messages={messages} />
      </div>
      <Composer onSend={handleSend} disabled={sending || !jwt} />
    </main>
  );
}
