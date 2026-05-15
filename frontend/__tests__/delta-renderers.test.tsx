import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { CitationBadge } from "@/components/citation-badge";
import { Disclaimer } from "@/components/disclaimer";
import { RecommendationCard } from "@/components/recommendation-card";
import { SectionBlock } from "@/components/section-block";
import { StockCard } from "@/components/stock-card";

describe("delta renderers", () => {
  it("renders stock card with stats", () => {
    render(
      <StockCard delta={{
        type: "stock_card", ticker: "AAPL", name: "Apple Inc.",
        market: "US", currency: "USD",
        stats: { "P/E": "29.5", "Net margin": "25.5%" },
      }} />,
    );
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Apple Inc. · US")).toBeInTheDocument();
    expect(screen.getByText("29.5")).toBeInTheDocument();
    expect(screen.getByText("25.5%")).toBeInTheDocument();
  });

  it("renders section markdown + citation badges", () => {
    render(
      <SectionBlock delta={{
        type: "section", title: "Fundamentals",
        markdown: "Net margin **25.5%**",
        citations: [{ source: "yfinance", ref: "yfinance:ratios:AAPL", index: 1 }],
      }} />,
    );
    expect(screen.getByText("Fundamentals")).toBeInTheDocument();
    expect(screen.getByText(/25\.5/)).toBeInTheDocument();
    expect(screen.getByText("yfinance·1")).toBeInTheDocument();
  });

  it("renders recommendation card with all fields", () => {
    render(
      <RecommendationCard delta={{
        type: "recommendation", signal: "tactical_buy",
        position_size_range: [2, 4], entry_zone: "440-455",
        stop: "385", target_12mo_base: "540",
      }} />,
    );
    expect(screen.getByText("Tactical Buy")).toBeInTheDocument();
    expect(screen.getByText("2–4%")).toBeInTheDocument();
    expect(screen.getByText("440-455")).toBeInTheDocument();
    expect(screen.getByText("385")).toBeInTheDocument();
    expect(screen.getByText("540")).toBeInTheDocument();
  });

  it("renders citation badge with index label", () => {
    render(<CitationBadge citation={{ source: "edgar", ref: "AAPL 10-K", index: 3 }} />);
    expect(screen.getByText("edgar·3")).toBeInTheDocument();
  });

  it("renders disclaimer text", () => {
    render(<Disclaimer delta={{ type: "disclaimer", text: "Educational analysis…" }} />);
    expect(screen.getByText("Educational analysis…")).toBeInTheDocument();
  });
});
