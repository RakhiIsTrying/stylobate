import { createClient } from "@/lib/supabase/client";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) throw new Error("Not authenticated");

  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
      ...init.headers,
    },
  });

  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      (body as { detail?: unknown; message?: unknown })?.detail ??
      (body as { message?: unknown })?.message ??
      res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}
