'use client';

import { ColorType, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import type { Point } from '@/lib/types';

export const CHART_THEME = {
  layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8b949e' },
  grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
  rightPriceScale: { borderColor: '#30363d' },
  timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: true },
} as const;

/** Generic line chart over a Point[] (used for the main price chart and the P&L chart). */
export function PriceChart({ points, color, testId }: { points: Point[]; color: string; testId?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!el.current) return;
    chart.current = createChart(el.current, { autoSize: true, ...CHART_THEME });
    series.current = chart.current.addLineSeries({ lineWidth: 2, color });
    return () => { chart.current?.remove(); chart.current = null; series.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    series.current?.applyOptions({ color });
    series.current?.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
  });

  return <div ref={el} className="h-full w-full" data-testid={testId} />;
}
