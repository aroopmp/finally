'use client';

import { fmtNum, fmtPct, signClass } from '@/lib/format';
import { PriceChart } from './PriceChart';
import { useTrading } from './TradingProvider';

export function MainChart() {
  const { selected, prices, history, version } = useTrading();
  void version;
  const quote = selected ? prices[selected] : undefined;
  const points = selected ? [...(history.current.get(selected) ?? [])] : [];
  const up = (quote?.day_change_pct ?? 0) >= 0;

  return (
    <section className="panel flex h-full min-h-0 flex-col" aria-label="Price chart">
      <div className="flex items-baseline gap-3 px-3 py-2">
        <span className="panel-title">Chart</span>
        <span data-testid="chart-ticker" className="font-semibold">{selected ?? '—'}</span>
        {quote && (
          <>
            <span className="num text-base">{fmtNum(quote.price)}</span>
            <span className={`num text-xs ${signClass(quote.day_change_pct)}`}>{fmtPct(quote.day_change_pct)}</span>
          </>
        )}
        <span className="ml-auto text-[11px] text-muted">since page load</span>
      </div>
      <div className="min-h-0 flex-1 px-1 pb-1">
        {points.length > 1 ? (
          <PriceChart points={points} color={up ? '#3fb950' : '#f85149'} testId="main-chart" />
        ) : (
          <div className="flex h-full items-center justify-center text-muted" data-testid="main-chart-empty">
            Waiting for price data…
          </div>
        )}
      </div>
    </section>
  );
}
