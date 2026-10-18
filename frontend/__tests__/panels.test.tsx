import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PositionsTable } from '@/components/PositionsTable';
import { TradeBar } from '@/components/TradeBar';
import { Watchlist } from '@/components/Watchlist';
import { livePosition } from '@/components/TradingProvider';
import type { Position } from '@/lib/types';

const ctx = {
  watchlist: [{ ticker: 'AAPL', added_at: '', quote: null }, { ticker: 'MSFT', added_at: '', quote: null }],
  prices: {} as Record<string, unknown>,
  history: { current: new Map() },
  version: 0,
  selected: 'AAPL',
  select: vi.fn(),
  addTicker: vi.fn(),
  removeTicker: vi.fn(),
  trade: vi.fn(),
  positions: [] as unknown[],
};
vi.mock('@/components/TradingProvider', async (orig) => ({
  ...(await orig<typeof import('@/components/TradingProvider')>()),
  useTrading: () => ctx,
}));
vi.mock('@/components/Sparkline', () => ({ Sparkline: () => null }));

beforeEach(() => {
  ctx.addTicker.mockReset().mockResolvedValue(undefined);
  ctx.removeTicker.mockReset().mockResolvedValue(undefined);
  ctx.trade.mockReset();
  ctx.select.mockReset();
  ctx.prices = {};
  ctx.positions = [];
});

describe('Watchlist CRUD', () => {
  it('lists tickers and selects on click', async () => {
    render(<Watchlist />);
    await userEvent.click(screen.getByTestId('watchlist-row-MSFT'));
    expect(ctx.select).toHaveBeenCalledWith('MSFT');
  });

  it('adds a ticker and clears the input', async () => {
    render(<Watchlist />);
    await userEvent.type(screen.getByTestId('watchlist-add-input'), 'pypl');
    await userEvent.click(screen.getByTestId('watchlist-add'));
    expect(ctx.addTicker).toHaveBeenCalledWith('pypl');
    await waitFor(() => expect(screen.getByTestId('watchlist-add-input')).toHaveValue(''));
  });

  it('shows the error when add fails', async () => {
    ctx.addTicker.mockRejectedValue(new Error('Unknown ticker ZZZZ'));
    render(<Watchlist />);
    await userEvent.type(screen.getByTestId('watchlist-add-input'), 'ZZZZ');
    await userEvent.click(screen.getByTestId('watchlist-add'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown ticker ZZZZ');
  });

  it('removes a ticker without selecting it', async () => {
    render(<Watchlist />);
    await userEvent.click(screen.getByTestId('watchlist-remove-AAPL'));
    expect(ctx.removeTicker).toHaveBeenCalledWith('AAPL');
    expect(ctx.select).not.toHaveBeenCalled();
  });
});

describe('TradeBar', () => {
  it('defaults to the selected ticker and submits a buy', async () => {
    ctx.trade.mockResolvedValue('Bought 5 AAPL @ $190.00');
    render(<TradeBar />);
    expect(screen.getByTestId('trade-ticker')).toHaveValue('AAPL');
    await userEvent.type(screen.getByTestId('trade-qty'), '5');
    await userEvent.click(screen.getByTestId('trade-buy'));
    expect(ctx.trade).toHaveBeenCalledWith('AAPL', 5, 'buy');
    expect(await screen.findByTestId('trade-notice')).toHaveTextContent('Bought 5 AAPL');
  });

  it('shows API errors and validates empty quantity', async () => {
    render(<TradeBar />);
    await userEvent.click(screen.getByTestId('trade-sell'));
    expect(screen.getByTestId('trade-notice')).toHaveTextContent('Enter a ticker and quantity');
    ctx.trade.mockRejectedValue(new Error('Insufficient cash'));
    await userEvent.type(screen.getByTestId('trade-qty'), '1000');
    await userEvent.click(screen.getByTestId('trade-buy'));
    expect(await screen.findByText('Insufficient cash')).toBeInTheDocument();
  });
});

const pos = (over: Partial<Position> = {}): Position => ({
  ticker: 'AAPL', quantity: 10, avg_cost: 100, price: 110, market_value: 1100,
  unrealized_pnl: 100, unrealized_pnl_pct: 10, day_change_pct: 1, stale: false, ...over,
});

describe('portfolio calculations', () => {
  it('uses the live stream price for value and P&L', () => {
    const p = livePosition(pos(), 120);
    expect(p.liveValue).toBe(1200);
    expect(p.livePnl).toBe(200);
    expect(p.livePnlPct).toBeCloseTo(20);
  });

  it('computes a loss', () => {
    const p = livePosition(pos(), 90);
    expect(p.livePnl).toBe(-100);
    expect(p.livePnlPct).toBeCloseTo(-10);
  });

  it('falls back to server price, then to cost when unpriced', () => {
    expect(livePosition(pos(), undefined).livePrice).toBe(110);
    const none = livePosition(pos({ price: null }), undefined);
    expect(none.liveValue).toBe(1000);
    expect(none.livePnl).toBeNull();
  });
});

describe('PositionsTable', () => {
  it('shows an empty state', () => {
    render(<PositionsTable />);
    expect(screen.getByText(/No positions/)).toBeInTheDocument();
  });

  it('renders a row per position', () => {
    ctx.positions = [livePosition(pos(), 120)];
    render(<PositionsTable />);
    expect(screen.getByTestId('position-row-AAPL')).toHaveTextContent('AAPL');
    expect(screen.getByTestId('position-qty-AAPL')).toHaveTextContent('10');
    expect(screen.getByTestId('position-row-AAPL')).toHaveTextContent('+20.00%');
  });
});
