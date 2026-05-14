import { describe, it, expect } from "vitest";
import { parseSSEStream } from "@/lib/sse";
import type { SSEEvent } from "@/lib/types";

describe("parseSSEStream", () => {
  it("parses a simple delta + done stream", async () => {
    const chunks = [
      'event: progress\ndata: {"step":"received"}\n\n',
      'event: delta\ndata: {"type":"text","text":"echo: hi"}\n\n',
      'event: done\ndata: {"message_id":"m1","chat_id":"c1"}\n\n',
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        for (const c of chunks) controller.enqueue(enc.encode(c));
        controller.close();
      },
    });
    const events: SSEEvent[] = [];
    for await (const ev of parseSSEStream(stream)) events.push(ev);
    expect(events).toHaveLength(3);
    expect(events[0]).toEqual({ event: "progress", data: { step: "received" } });
    expect(events[1]).toEqual({ event: "delta", data: { type: "text", text: "echo: hi" } });
    expect(events[2]).toEqual({ event: "done", data: { message_id: "m1", chat_id: "c1" } });
  });

  it("handles chunked frames", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        controller.enqueue(enc.encode("event: delt"));
        controller.enqueue(enc.encode('a\ndata: {"type":"text","text":"hi"}\n'));
        controller.enqueue(enc.encode("\n"));
        controller.close();
      },
    });
    const events: SSEEvent[] = [];
    for await (const ev of parseSSEStream(stream)) events.push(ev);
    expect(events).toEqual([{ event: "delta", data: { type: "text", text: "hi" } }]);
  });
});
