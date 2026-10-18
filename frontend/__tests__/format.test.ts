import { describe, expect, it } from 'vitest';
import { fmtPct, fmtQty, fmtUsd, signClass, sourceLabel } from '@/lib/format';
import { livePosition } from '@/components/TradingProvider';
import { pnlColor, treemapData } from '@/components/Heatmap';
import { snapshotPoints } from '@/components/PnLChart';

describe('format', () => {
  it('formats money, quantity, percent', () => {
    expect(fmtUsd(1234.5)).toBe('$1,234.50');
    expect(fmtUsd(null)).toBe('—');
    expect(fmtQty(0.0101)).toBe('0.0101');
    expect(fmtPct(1.234)).toBe('+1.23%');
    expect(fmtPct(-0.5)).toBe('-0.50%');
    expect(fmtPct(null)).toBe('—');
  });
  it('sign colours', () => {
    expect(signClass(1)).toBe('text-up');
    expect(signClass(-1)).toBe('text-down');
    expect(signClass(0)).toBe('text-muted');
  });
  it('labels sources', () => {
    expect(sourceLabel('massive-eod')).toBe('End-of-day prices');
    expect(sourceLabel('nope')).toBeNull();
  });
});

const pos = { ticker: 'AAPL', quantity: 10, avg_cost: 100, price: 100, market_value: 1000, unrealized_pnl: 0, unrealized_pnl_pct: 0, day_change_pct: 0, stale: false };

describe('portfolio maths', () => {
  it('values a position at the live price', () => {
    const p = livePosition(pos, 110);
    expect(p.liveValue).toBe(1100);
    expect(p.livePnl).toBe(100);
    expect(p.livePnlPct).toBeCloseTo(10);
  });
  it('falls back to cost basis, not zero, without any price', () => {
    const p = livePosition({ ...pos, price: null }, undefined);
    expect(p.liveValue).toBe(1000);
    expect(p.livePnl).toBeNull();
  });
});

describe('heatmap and pnl chart data', () => {
  it('colours profit green and loss red', () => {
    expect(pnlColor(5)).toContain('63, 185, 80');
    expect(pnlColor(-5)).toContain('248, 81, 73');
  });
  it('sizes treemap by value', () => {
    expect(treemapData([livePosition(pos, 120)])).toEqual([{ name: 'AAPL', size: 1200, pnlPct: 20 }]);
  });
  it('dedupes snapshots within a second and keeps order', () => {
    const pts = snapshotPoints([
      { total_value: 1, recorded_at: '2026-01-01T00:00:00.100Z' },
      { total_value: 2, recorded_at: '2026-01-01T00:00:00.900Z' },
      { total_value: 3, recorded_at: '2026-01-01T00:00:05.000Z' },
    ]);
    expect(pts.map((p) => p.value)).toEqual([2, 3]);
  });
});
