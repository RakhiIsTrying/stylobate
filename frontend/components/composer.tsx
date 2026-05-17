"use client";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";

export function Composer({
  onSend,
  disabled,
  initialValue,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  initialValue?: string;
}) {
  const [value, setValue] = useState(initialValue ?? "");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!value.trim() || disabled) return;
    onSend(value.trim());
    setValue("");
  }

  return (
    <form onSubmit={submit} className="flex gap-2 border-t bg-card p-4">
      <input
        type="text"
        className="flex-1 rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
        placeholder="Ask anything — for now I just echo back."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={disabled}
      />
      <Button type="submit" disabled={disabled}>Send</Button>
    </form>
  );
}
