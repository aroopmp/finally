'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { usePriceStream, type PriceStream } from '@/hooks/usePriceStream';
import { api } from '@/lib/api';
import type { Portfolio, Position, Snapshot, WatchlistItem } from '@/lib/types';

export interface LivePosition extends Position {
  livePrice: number | null;
  liveValue: number;
  livePnl: number | null;
  livePnlPct: number | null;
}

interface TradingContextValue extends PriceStream {
  portfolio: Portfolio | null;
  watchlist: WatchlistItem[];
  snapshots: Snapshot[];
  positions: LivePosition[];
  totalValue: number;
  unrealizedPnl: number;
  selected: string | null;
  select: (ticker: string) => void;
  refreshAll: () => Promise<void>;
  trade: (ticker: string, quantity: number, side: 'buy' | 'sell') => Promise<string>;
  addTicker: (ticker: string) => Promise<void>;
  removeTicker: (ticker: string) => Promise<void>;
  reset: () => Promise<void>;
  /** Incremented on every reset so panels with local state (chat) can clear. */
  resetCount: number;
}

const Ctx = createContext<TradingContextValue | null>(null);

export function useTrading(): TradingContextValue {
  const value = useContext(Ctx);
  if (!value) throw new Error('useTrading must be used inside TradingProvider');
  return value;
}

export function livePosition(p: Position, price: number | undefined): LivePosition {
  const livePrice = price ?? p.price;
  const cost = p.quantity * p.avg_cost;
  const liveValue = livePrice === null ? cost : p.quantity * livePrice; // never silently zero
  const livePnl = livePrice === null ? null : liveValue - cost;
  const livePnlPct = livePrice === null || !p.avg_cost ? null : ((livePrice - p.avg_cost) / p.avg_cost) * 100;
  return { ...p, livePrice, liveValue, livePnl, livePnlPct };
}

export function TradingProvider({ children }: { children: React.ReactNode }) {
  const stream = usePriceStream();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [resetCount, setResetCount] = useState(0);

  const refreshPortfolio = useCallback(async () => setPortfolio(await api.portfolio()), []);
  const refreshWatchlist = useCallback(async () => {
    const { items } = await api.watchlist();
    setWatchlist(items);
    setSelected((cur) => (cur && items.some((i) => i.ticker === cur) ? cur : items[0]?.ticker ?? null));
  }, []);
  const refreshHistory = useCallback(async () => setSnapshots((await api.history()).snapshots), []);

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([refreshPortfolio(), refreshWatchlist(), refreshHistory()]);
  }, [refreshPortfolio, refreshWatchlist, refreshHistory]);

  useEffect(() => {
    void refreshAll();
    const id = setInterval(() => {
      void refreshPortfolio().catch(() => {});
      void refreshHistory().catch(() => {});
    }, 15000);
    return () => clearInterval(id);
  }, [refreshAll, refreshPortfolio, refreshHistory]);

  const positions = useMemo(
    () => (portfolio?.positions ?? []).map((p) => livePosition(p, stream.prices[p.ticker]?.price)),
    [portfolio, stream.prices],
  );
  const totalValue = (portfolio?.cash_balance ?? 0) + positions.reduce((s, p) => s + p.liveValue, 0);
  const unrealizedPnl = positions.reduce((s, p) => s + (p.livePnl ?? 0), 0);

  const trade = useCallback(
    async (ticker: string, quantity: number, side: 'buy' | 'sell') => {
      const res = await api.trade(ticker, quantity, side);
      await refreshAll();
      const t = res.trade;
      return `${side === 'buy' ? 'Bought' : 'Sold'} ${t.quantity} ${t.ticker} @ $${t.price.toFixed(2)}`;
    },
    [refreshAll],
  );

  const value: TradingContextValue = {
    ...stream,
    portfolio, watchlist, snapshots, positions, totalValue, unrealizedPnl, selected,
    select: setSelected,
    refreshAll,
    trade,
    addTicker: async (ticker) => { await api.addTicker(ticker); await refreshAll(); },
    removeTicker: async (ticker) => { await api.removeTicker(ticker); await refreshAll(); },
    reset: async () => { await api.reset(); setResetCount((n) => n + 1); await refreshAll(); },
    resetCount,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
