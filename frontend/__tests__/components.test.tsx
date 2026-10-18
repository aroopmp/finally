import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatBubble, ChatPanel, describeAction } from '@/components/ChatPanel';
import { ConnectionDot } from '@/components/Header';
import type { ChatAction } from '@/lib/types';

const refreshAll = vi.fn(async () => {});
vi.mock('@/components/TradingProvider', () => ({
  useTrading: () => ({ refreshAll, resetCount: 0 }),
}));
const chat = vi.fn();
const chatHistory = vi.fn();
vi.mock('@/lib/api', () => ({ api: { chat: (m: string) => chat(m), chatHistory: () => chatHistory() } }));

beforeEach(() => {
  chat.mockReset();
  chatHistory.mockReset().mockResolvedValue({ messages: [] });
  refreshAll.mockClear();
});

describe('describeAction', () => {
  it('renders successful and failed actions independently', () => {
    const ok: ChatAction = { type: 'trade', ticker: 'AAPL', side: 'buy', quantity: 10, status: 'ok', price: 190, total: 1900 };
    const bad: ChatAction = { type: 'trade', ticker: 'TSLA', side: 'buy', quantity: 1000, status: 'error', error: { code: 'insufficient_cash', message: 'Need more cash' } };
    expect(describeAction(ok)).toEqual({ ok: true, text: 'Bought 10 AAPL @ $190.00' });
    expect(describeAction(bad)).toEqual({ ok: false, text: 'Buy 1,000 TSLA failed: Need more cash' });
    expect(describeAction({ type: 'watchlist', ticker: 'PYPL', action: 'add', status: 'ok' }).text).toBe('Added PYPL to watchlist');
  });
});

describe('ChatBubble', () => {
  it('shows action confirmations inline under the message', () => {
    render(<ChatBubble m={{ id: '1', role: 'assistant', content: 'Done', created_at: '', actions: [{ type: 'trade', ticker: 'AAPL', side: 'sell', quantity: 2, status: 'ok', price: 200, total: 400 }] }} />);
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByTestId('chat-action')).toHaveTextContent('Sold 2 AAPL @ $200.00');
    expect(screen.getByTestId('chat-action')).toHaveAttribute('data-status', 'ok');
  });
});

describe('ChatPanel', () => {
  it('sends a message, shows a loading indicator, then the reply', async () => {
    let resolve!: (v: unknown) => void;
    chat.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<ChatPanel />);
    await userEvent.type(screen.getByTestId('chat-input'), 'buy 1 AAPL');
    await userEvent.click(screen.getByTestId('chat-send'));
    expect(screen.getByTestId('chat-user')).toHaveTextContent('buy 1 AAPL');
    expect(screen.getByTestId('chat-loading')).toBeInTheDocument();
    resolve({ id: 'a', role: 'assistant', content: 'Placing it', created_at: '', actions: [] });
    await waitFor(() => expect(screen.getByText('Placing it')).toBeInTheDocument());
    expect(screen.queryByTestId('chat-loading')).toBeNull();
    expect(refreshAll).toHaveBeenCalled();
  });

  it('shows a plain error bubble when the request fails', async () => {
    chat.mockRejectedValue(new Error('The assistant is unavailable right now.'));
    render(<ChatPanel />);
    await userEvent.type(screen.getByTestId('chat-input'), 'hi');
    await userEvent.click(screen.getByTestId('chat-send'));
    await waitFor(() => expect(screen.getByText('The assistant is unavailable right now.')).toBeInTheDocument());
  });

  it('restores history on load', async () => {
    chatHistory.mockResolvedValue({ messages: [{ id: '1', role: 'user', content: 'earlier', actions: null, created_at: '' }] });
    render(<ChatPanel />);
    await waitFor(() => expect(screen.getByText('earlier')).toBeInTheDocument());
  });
});

describe('ConnectionDot', () => {
  it.each([['connected', 'bg-up'], ['reconnecting', 'bg-accent'], ['disconnected', 'bg-down']] as const)('%s uses %s', (status, cls) => {
    render(<ConnectionDot status={status} />);
    expect(screen.getByTestId('conn-dot')).toHaveClass(cls);
  });
});
