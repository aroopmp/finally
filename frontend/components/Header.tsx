'use client';

import { fmtPct, fmtUsd, signClass, sourceLabel } from '@/lib/format';
import { useTrading } from './TradingProvider';
import type { ConnectionStatus } from '@/lib/types';

const DOT: Record<ConnectionStatus, { cls: string; label: string }> = {
  connected: { cls: 'bg-up', label: 'Connected' },
  reconnecting: { cls: 'bg-accent', label: 'Reconnecting' },
  disconnected: { cls: 'bg-down', label: 'Disconnected' },
};

export function ConnectionDot({ status }: { status: ConnectionStatus }) {
  const d = DOT[status];
  return (
    <span className="flex items-center gap-1.5 text-muted" title={d.label}>
      <span data-testid="conn-dot" data-status={status} className={`inline-block h-2.5 w-2.5 rounded-full ${d.cls}`} />
      <span className="hidden text-xs sm:inline">{d.label}</span>
    </span>
  );
}

export function Header() {
  const { portfolio, totalValue, unrealizedPnl, status, reset } = useTrading();
  const cash = portfolio?.cash_balance ?? 0;
  const cost = totalValue - unrealizedPnl - cash;
  const pnlPct = cost > 0 ? (unrealizedPnl / cost) * 100 : 0;
  const label = sourceLabel(portfolio?.market.source);

  const onReset = async () => {
    if (window.confirm('Reset cash to $10,000 and clear positions, trades and chat?')) await reset();
  };

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-panel px-4 py-2">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg font-bold tracking-tight text-accent">
          Fin<span className="text-primary">Ally</span>
        </h1>
        {label && (
          <span data-testid="market-source" className="rounded border border-line px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted">
            {label}
          </span>
        )}
      </div>
      <div className="flex items-center gap-6">
        <Stat label="Portfolio" testId="header-total" value={fmtUsd(totalValue)} big />
        <Stat label="Cash" testId="header-cash" value={fmtUsd(cash)} />
        <Stat label="Unrealized P&L" testId="header-pnl" value={`${fmtUsd(unrealizedPnl)} (${fmtPct(pnlPct)})`} cls={signClass(unrealizedPnl)} />
        <ConnectionDot status={status} />
        <button onClick={onReset} data-testid="reset" className="rounded border border-line px-2 py-1 text-xs text-muted hover:border-down hover:text-down">
          Reset
        </button>
      </div>
    </header>
  );
}

function Stat({ label, value, testId, cls = '', big = false }: { label: string; value: string; testId: string; cls?: string; big?: boolean }) {
  return (
    <div className="text-right leading-tight">
      <div className="panel-title">{label}</div>
      <div data-testid={testId} className={`num ${big ? 'text-lg font-semibold' : 'text-sm'} ${cls}`}>{value}</div>
    </div>
  );
}
