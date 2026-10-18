'use client';

import { ResponsiveContainer, Treemap } from 'recharts';
import { useTrading, type LivePosition } from './TradingProvider';

export function pnlColor(pct: number | null): string {
  const p = pct ?? 0;
  const alpha = 0.25 + Math.min(Math.abs(p) / 10, 1) * 0.6;
  return p >= 0 ? `rgba(63, 185, 80, ${alpha.toFixed(2)})` : `rgba(248, 81, 73, ${alpha.toFixed(2)})`;
}

interface CellProps { x?: number; y?: number; width?: number; height?: number; name?: string; pnlPct?: number | null }

function Cell({ x = 0, y = 0, width = 0, height = 0, name, pnlPct = null }: CellProps) {
  if (!name) return <g />;
  return (
    <g data-testid={`heat-${name}`} data-pnl={pnlPct === null ? '' : pnlPct.toFixed(2)}>
      <rect x={x} y={y} width={width} height={height} fill={pnlColor(pnlPct)} stroke="#0d1117" strokeWidth={2} />
      {width > 50 && height > 34 && (
        <>
          <text x={x + 8} y={y + 18} fill="#e6edf3" fontSize={13} fontWeight={600}>{name}</text>
          <text x={x + 8} y={y + 34} fill="#e6edf3" fontSize={11}>
            {pnlPct === null ? '—' : `${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%`}
          </text>
        </>
      )}
    </g>
  );
}

export function treemapData(positions: LivePosition[]) {
  return positions.map((p) => ({ name: p.ticker, size: Math.max(p.liveValue, 0.01), pnlPct: p.livePnlPct }));
}

export function Heatmap() {
  const { positions } = useTrading();
  const data = treemapData(positions);
  return (
    <section className="panel flex h-full min-h-0 flex-col" aria-label="Portfolio heatmap">
      <div className="px-3 py-2"><span className="panel-title">Positions heatmap</span></div>
      <div className="min-h-0 flex-1 p-1" data-testid="heatmap">
        {data.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted">No positions yet</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <Treemap data={data} dataKey="size" isAnimationActive={false} content={<Cell />} />
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}
