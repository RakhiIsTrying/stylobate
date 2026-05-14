import type { SSEEvent } from "./types";

export async function* parseSSEStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (parsed) yield parsed;
    }
  }
}

function parseFrame(frame: string): SSEEvent | null {
  const lines = frame.split("\n");
  let event = "";
  let data = "";
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!event) return null;
  try {
    return { event, data: JSON.parse(data) } as SSEEvent;
  } catch {
    return null;
  }
}

export async function postChatStream({
  jwt,
  content,
  chatId,
  backendUrl,
}: {
  jwt: string;
  content: string;
  chatId: string | null;
  backendUrl: string;
}): Promise<ReadableStream<Uint8Array>> {
  const resp = await fetch(`${backendUrl}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${jwt}`,
    },
    body: JSON.stringify({ content, chat_id: chatId }),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE request failed: ${resp.status}`);
  }
  return resp.body;
}
