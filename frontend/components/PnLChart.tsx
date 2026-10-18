'use client';

import { useMemo } from 'react';
import type { Point, Snapshot } from '@/lib/types';
import { PriceChart } from './PriceChart';
import { useTrading } from './TradingProvider';

/** Snapshots -> strictly increasing whole-second points (latest value wins within a second). */
export function snapshotPoints(snapshots: Snapshot[]): Point[] {
  const out: Point[] = [];
  for (const s of snapshots) {
    const time = Math.floor(new Date(s.recorded_at).getTime() / 1000);
    const last = out[out.length - 1];
    if (last && time === last.time) last.value = s.total_value;
    else if (!last || time > last.time) out.push({ time, value: s.total_value });
  }
  return out;
}

export function PnLChart() {
  const { snapshots } = useTrading();
  const points = useMemo(() => snapshotPoints(snapshots), [snapshots]);
  const last = points[points.length - 1]?.value ?? 10000;
  const first = points[0]?.value ?? 10000;
  return (
    <section className="panel flex h-full min-h-0 flex-col" aria-label="P&L chart">
      <div className="px-3 py-2"><span className="panel-title">Portfolio value</span></div>
      <div className="min-h-0 flex-1 px-1 pb-1" data-testid="pnl-chart" data-points={points.length}>
        {points.length > 0 ? (
          <PriceChart points={points.length === 1 ? [{ ...points[0], time: points[0].time - 1 }, points[0]] : points} color={last >= first ? '#3fb950' : '#f85149'} />
        ) : (
          <div className="flex h-full items-center justify-center text-muted">No history yet</div>
        )}
      </div>
    </section>
  );
}
