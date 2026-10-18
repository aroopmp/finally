'use client';

import { FormEvent, useState } from 'react';
import { fmtNum, fmtPct, signClass } from '@/lib/format';
import { FlashPrice } from './FlashPrice';
import { Sparkline } from './Sparkline';
import { useTrading } from './TradingProvider';

export function Watchlist() {
  const { watchlist, prices, history, version, selected, select, addTicker, removeTicker } = useTrading();
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  void version; // re-render on every stream update so sparklines read fresh history

  const onAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    try {
      await addTicker(input.trim());
      setInput('');
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <section className="panel flex h-full min-h-0 flex-col" aria-label="Watchlist">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="panel-title">Watchlist</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full">
          <tbody>
            {watchlist.map(({ ticker }) => {
              const q = prices[ticker];
              const points = history.current.get(ticker) ?? [];
              const dayPct = q?.day_change_pct ?? null;
              return (
                <tr
                  key={ticker}
                  data-testid={`watchlist-row-${ticker}`}
                  onClick={() => select(ticker)}
                  className={`cursor-pointer border-t border-line/60 hover:bg-panel2 ${selected === ticker ? 'bg-panel2' : ''}`}
                >
                  <td className="px-3 py-1.5 font-semibold">{ticker}</td>
                  <td className="py-1.5 text-right">
                    <FlashPrice price={q?.price} text={q ? fmtNum(q.price) : '—'} testId={`price-${ticker}`} />
                  </td>
                  <td className={`num py-1.5 pl-2 text-right text-xs ${signClass(dayPct)}`} data-testid={`change-${ticker}`}>
                    {fmtPct(dayPct)}
                  </td>
                  <td className="py-1.5 pl-2"><Sparkline points={points} up={(dayPct ?? 0) >= 0} /></td>
                  <td className="pr-2 text-right">
                    <button
                      aria-label={`Remove ${ticker}`}
                      data-testid={`watchlist-remove-${ticker}`}
                      onClick={(e) => { e.stopPropagation(); void removeTicker(ticker).catch((err) => setError(err.message)); }}
                      className="text-muted hover:text-down"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <form onSubmit={onAdd} className="border-t border-line p-2">
        <div className="flex gap-2">
          <input
            data-testid="watchlist-add-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Add ticker"
            maxLength={5}
            className="num w-full rounded border border-line bg-bg px-2 py-1 uppercase outline-none focus:border-primary"
          />
          <button data-testid="watchlist-add" className="rounded bg-secondary px-3 py-1 text-white hover:opacity-90">Add</button>
        </div>
        {error && <p role="alert" className="mt-1 text-xs text-down">{error}</p>}
      </form>
    </section>
  );
}
