"use client";

import { useState } from "react";
import { PortfolioTab } from "@/components/portfolio-tab";
import { WatchlistTab } from "@/components/watchlist-tab";

type Tab = "holdings" | "watchlist";

export default function PortfolioPage() {
  const [tab, setTab] = useState<Tab>("holdings");
  return (
    <div className="space-y-4">
      <div className="flex border-b">
        <button
          onClick={() => setTab("holdings")}
          className={`px-4 py-2 ${
            tab === "holdings"
              ? "border-b-2 border-blue-500 font-semibold"
              : "text-gray-500"
          }`}
        >
          Holdings
        </button>
        <button
          onClick={() => setTab("watchlist")}
          className={`px-4 py-2 ${
            tab === "watchlist"
              ? "border-b-2 border-blue-500 font-semibold"
              : "text-gray-500"
          }`}
        >
          Watchlist
        </button>
      </div>
      {tab === "holdings" ? <PortfolioTab /> : <WatchlistTab />}
    </div>
  );
}
