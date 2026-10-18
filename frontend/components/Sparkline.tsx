'use client';

import { ColorType, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import type { Point } from '@/lib/types';

const POINTS = 120;

export function Sparkline({ points, up }: { points: Point[]; up: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!el.current) return;
    chart.current = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: 'transparent' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false, borderVisible: false },
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    });
    series.current = chart.current.addLineSeries({
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    return () => { chart.current?.remove(); chart.current = null; series.current = null; };
  }, []);

  useEffect(() => {
    series.current?.applyOptions({ color: up ? '#3fb950' : '#f85149' });
    series.current?.setData(points.slice(-POINTS).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    chart.current?.timeScale().fitContent();
  });

  return <div ref={el} className="h-7 w-20" data-testid="sparkline" />;
}
