'use client';

import { ChatPanel } from '@/components/ChatPanel';
import { Header } from '@/components/Header';
import { Heatmap } from '@/components/Heatmap';
import { MainChart } from '@/components/MainChart';
import { PnLChart } from '@/components/PnLChart';
import { PositionsTable } from '@/components/PositionsTable';
import { TradeBar } from '@/components/TradeBar';
import { TradingProvider } from '@/components/TradingProvider';
import { Watchlist } from '@/components/Watchlist';

export default function Home() {
  return (
    <TradingProvider>
      <div className="flex min-h-screen flex-col lg:h-screen">
        <Header />
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-2 lg:flex-row">
          <div className="h-80 lg:h-auto lg:w-[340px] lg:shrink-0"><Watchlist /></div>
          <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
            <div className="h-72 lg:h-auto lg:min-h-0 lg:flex-[3]"><MainChart /></div>
            <div className="grid h-56 grid-cols-2 gap-2 lg:h-auto lg:min-h-0 lg:flex-[2]">
              <Heatmap />
              <PnLChart />
            </div>
            <TradeBar />
            <div className="h-48 lg:h-auto lg:min-h-0 lg:flex-[2]"><PositionsTable /></div>
          </main>
          <div className="h-96 lg:h-auto"><ChatPanel /></div>
        </div>
      </div>
    </TradingProvider>
  );
}
