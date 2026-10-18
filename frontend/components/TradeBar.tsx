'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useTrading } from './TradingProvider';

type Notice = { kind: 'ok' | 'error'; text: string } | null;

export function TradeBar() {
  const { trade, selected } = useTrading();
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  // Follow the selected ticker until the user types their own.
  const [touched, setTouched] = useState(false);
  useEffect(() => { if (!touched && selected) setTicker(selected); }, [selected, touched]);

  const submit = async (side: 'buy' | 'sell') => {
    const quantity = Number(qty);
    if (!ticker.trim() || !qty || Number.isNaN(quantity)) {
      setNotice({ kind: 'error', text: 'Enter a ticker and quantity' });
      return;
    }
    setBusy(true);
    try {
      setNotice({ kind: 'ok', text: await trade(ticker.trim(), quantity, side) });
    } catch (err) {
      setNotice({ kind: 'error', text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => e.preventDefault();

  return (
    <form onSubmit={onSubmit} className="panel flex flex-wrap items-center gap-2 px-3 py-2" aria-label="Trade">
      <span className="panel-title mr-1">Trade</span>
      <input
        data-testid="trade-ticker" aria-label="Ticker" placeholder="Ticker" maxLength={5} value={ticker}
        onChange={(e) => { setTouched(true); setTicker(e.target.value.toUpperCase()); }}
        className="num w-24 rounded border border-line bg-bg px-2 py-1 uppercase outline-none focus:border-primary"
      />
      <input
        data-testid="trade-qty" aria-label="Quantity" placeholder="Qty" inputMode="decimal" value={qty}
        onChange={(e) => setQty(e.target.value)}
        className="num w-24 rounded border border-line bg-bg px-2 py-1 outline-none focus:border-primary"
      />
      <button type="button" disabled={busy} data-testid="trade-buy" onClick={() => submit('buy')} className="rounded bg-secondary px-4 py-1 font-semibold text-white hover:opacity-90 disabled:opacity-50">Buy</button>
      <button type="button" disabled={busy} data-testid="trade-sell" onClick={() => submit('sell')} className="rounded border border-down px-4 py-1 font-semibold text-down hover:bg-down/10 disabled:opacity-50">Sell</button>
      <span role="status" aria-live="polite" data-testid="trade-notice" className={`text-xs ${notice?.kind === 'error' ? 'text-down' : 'text-up'}`}>
        {notice?.text}
      </span>
    </form>
  );
}
