'use client';

import { fmtNum, fmtPct, fmtQty, fmtUsd, signClass } from '@/lib/format';
import { useTrading } from './TradingProvider';

export function PositionsTable() {
  const { positions, select } = useTrading();
  return (
    <section className="panel flex h-full min-h-0 flex-col" aria-label="Positions">
      <div className="px-3 py-2"><span className="panel-title">Positions</span></div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full text-right">
          <thead className="sticky top-0 bg-panel text-[11px] uppercase text-muted">
            <tr>
              <th className="px-3 py-1 text-left font-normal">Ticker</th>
              <th className="px-2 font-normal">Qty</th>
              <th className="px-2 font-normal">Avg cost</th>
              <th className="px-2 font-normal">Price</th>
              <th className="px-2 font-normal">Value</th>
              <th className="px-2 font-normal">P&amp;L</th>
              <th className="px-3 font-normal">% chg</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-4 text-center text-muted">No positions. Use the trade bar or ask the assistant.</td></tr>
            )}
            {positions.map((p) => (
              <tr key={p.ticker} data-testid={`position-row-${p.ticker}`} onClick={() => select(p.ticker)} className="cursor-pointer border-t border-line/60 hover:bg-panel2">
                <td className="px-3 py-1 text-left font-semibold">{p.ticker}</td>
                <td className="num px-2" data-testid={`position-qty-${p.ticker}`}>{fmtQty(p.quantity)}</td>
                <td className="num px-2">{fmtNum(p.avg_cost)}</td>
                <td className="num px-2">{p.livePrice === null ? '—' : fmtNum(p.livePrice)}</td>
                <td className="num px-2">{fmtUsd(p.liveValue)}</td>
                <td className={`num px-2 ${signClass(p.livePnl)}`}>{p.livePnl === null ? '—' : fmtUsd(p.livePnl)}</td>
                <td className={`num px-3 ${signClass(p.livePnlPct)}`}>{fmtPct(p.livePnlPct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
