"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  deletePosition,
  listPortfolios,
  listPositions,
  type Portfolio,
  type Position,
  type PositionsResponse,
} from "@/lib/api-portfolios";
import { refreshPrices } from "@/lib/api-prices";
import { AddPositionModal } from "./add-position-modal";
import { CohortCard } from "./cohort-card";
import { EditPositionModal } from "./edit-position-modal";
import { PortfolioSelector } from "./portfolio-selector";
import { PortfolioTable } from "./portfolio-table";
import { RefreshButton } from "./refresh-button";

export function PortfolioTab() {
  const router = useRouter();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [data, setData] = useState<PositionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<Position | null>(null);

  function handleAnalyze() {
    if (!selectedId) return;
    const portfolio = portfolios.find((p) => p.id === selectedId);
    const name = portfolio?.name ?? "my portfolio";
    const prefill = `Analyze ${name}: cohort snapshot, returns vs benchmark, and risk metrics. Review my portfolio.`;
    router.push(`/chat?prefill=${encodeURIComponent(prefill)}`);
  }

  const reloadPositions = useCallback(async () => {
    if (!selectedId) return;
    try {
      const next = await listPositions(selectedId);
      setData(next);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selectedId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pf = await listPortfolios();
        if (cancelled) return;
        setPortfolios(pf);
        setSelectedId((prev) => prev ?? (pf.length > 0 ? pf[0].id : null));
      } catch (e: unknown) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!selectedId) {
        // yield so this setState is async w.r.t. the effect call site
        await Promise.resolve();
        if (cancelled) return;
        setData(null);
        setLoading(false);
        return;
      }
      try {
        const next = await listPositions(selectedId);
        if (cancelled) return;
        setData(next);
        setError(null);
      } catch (e: unknown) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function handleRefresh() {
    if (!data || data.positions.length === 0) return;
    const pairs = data.positions.map(
      (p) => [p.ticker, p.market] as [string, typeof p.market],
    );
    await refreshPrices(pairs);
    await reloadPositions();
  }

  async function handleDelete(p: Position) {
    if (!confirm(`Delete ${p.ticker}?`)) return;
    try {
      await deletePosition(p.id);
      await reloadPositions();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (portfolios.length === 0 && !loading) {
    return (
      <div className="border border-dashed rounded-md p-8 text-center space-y-3">
        <p className="text-muted-foreground">Welcome. No portfolios yet.</p>
        <div className="flex justify-center">
          <PortfolioSelector
            portfolios={portfolios}
            selectedId={null}
            onSelect={setSelectedId}
            onCreate={(p) => {
              setPortfolios([p]);
              setSelectedId(p.id);
            }}
          />
        </div>
        {error && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded-md text-sm">
            {error}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center flex-wrap gap-2">
        <PortfolioSelector
          portfolios={portfolios}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreate={(p) => {
            setPortfolios([...portfolios, p]);
            setSelectedId(p.id);
          }}
        />
        <div className="flex gap-2">
          <RefreshButton onRefresh={handleRefresh} />
          <button
            onClick={handleAnalyze}
            disabled={!selectedId}
            className="border rounded-md px-3 py-1 text-sm disabled:opacity-50"
          >
            Analyze portfolio
          </button>
          <button
            onClick={() => setAddOpen(true)}
            className="border rounded-md px-3 py-1 text-sm bg-blue-500 text-white hover:bg-blue-600"
          >
            + Add position
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 px-3 py-2 rounded-md text-sm">
          {error}
        </div>
      )}

      {data && data.cohorts.length > 0 && (
        <div className="flex gap-3 flex-wrap">
          {data.cohorts.map((c) => (
            <CohortCard key={`${c.currency}:${c.asset_class_group}`} c={c} />
          ))}
        </div>
      )}

      {data && (
        <PortfolioTable
          positions={data.positions}
          cohorts={data.cohorts}
          onEdit={setEditing}
          onDelete={handleDelete}
        />
      )}

      {data?.prices_partial && (
        <div className="bg-yellow-50 text-yellow-800 px-3 py-2 rounded-md text-sm">
          Prices temporarily unavailable for some tickers. Click Refresh to
          retry.
        </div>
      )}

      {addOpen && selectedId && (
        <AddPositionModal
          portfolioId={selectedId}
          onClose={() => setAddOpen(false)}
          onSaved={() => {
            void reloadPositions();
          }}
        />
      )}
      {editing && (
        <EditPositionModal
          position={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            void reloadPositions();
          }}
        />
      )}
    </div>
  );
}
