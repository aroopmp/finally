const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const num = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const qty = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });

const DASH = '—';
const valid = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n);

export const fmtUsd = (n: number | null | undefined): string => (valid(n) ? usd.format(n) : DASH);
export const fmtNum = (n: number | null | undefined): string => (valid(n) ? num.format(n) : DASH);
export const fmtQty = (n: number | null | undefined): string => (valid(n) ? qty.format(n) : DASH);

export function fmtPct(n: number | null | undefined): string {
  if (!valid(n)) return DASH;
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
}

export function signClass(n: number | null | undefined): string {
  if (!valid(n) || n === 0) return 'text-muted';
  return n > 0 ? 'text-up' : 'text-down';
}

const SOURCE_LABELS: Record<string, string> = {
  simulator: 'Simulated prices',
  'massive-realtime': 'Live prices',
  'massive-delayed': 'Delayed prices (~15 min)',
  'massive-eod': 'End-of-day prices',
};

export function sourceLabel(source: string | null | undefined): string | null {
  return (source && SOURCE_LABELS[source]) || null;
}
