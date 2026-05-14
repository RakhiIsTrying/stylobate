"use client";
import { useEffect, useState } from "react";

import { ChatThread } from "@/components/chat-thread";
import { Composer } from "@/components/composer";
import { parseSSEStream, postChatStream } from "@/lib/sse";
import { createClient } from "@/lib/supabase/client";
import type { ChatMessage } from "@/lib/types";

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
      const asstMsg: ChatMessage = { id: asstId, role: "assistant", content: [] };
      setMessages((m) => [...m, asstMsg]);
      let buf: ChatMessage["content"] = [];

      for await (const ev of parseSSEStream(stream)) {
        if (ev.event === "delta" && ev.data.type === "text") {
          buf = Array.isArray(buf) ? [...buf, ev.data] : [ev.data];
          setMessages((m) =>
            m.map((x) => (x.id === asstId ? { ...x, content: buf } : x)),
          );
        } else if (ev.event === "done") {
          setChatId(ev.data.chat_id);
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
