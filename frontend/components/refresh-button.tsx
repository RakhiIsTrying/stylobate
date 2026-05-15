"use client";

import { useState } from "react";

export function RefreshButton({
  onRefresh,
}: {
  onRefresh: () => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  return (
    <button
      onClick={async () => {
        setLoading(true);
        try {
          await onRefresh();
        } finally {
          setLoading(false);
        }
      }}
      disabled={loading}
      className="border rounded-md px-3 py-1 text-sm hover:bg-muted disabled:opacity-50"
    >
      {loading ? "Refreshing..." : "Refresh prices"}
    </button>
  );
}
