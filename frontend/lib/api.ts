import type { ChatMessage, Portfolio, Snapshot, TradeResult, WatchlistItem } from './types';

/** Error carrying the backend's `{error: {code, message}}` envelope. */
export class ApiRequestError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiRequestError(0, 'network_error', 'Cannot reach the server.');
  }
  if (!res.ok) {
    let code = 'http_error';
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) ({ code, message } = body.error);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiRequestError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  portfolio: () => request<Portfolio>('/api/portfolio'),
  history: (limit = 2000) => request<{ snapshots: Snapshot[] }>(`/api/portfolio/history?limit=${limit}`),
  trade: (ticker: string, quantity: number, side: 'buy' | 'sell') =>
    post<TradeResult>('/api/portfolio/trade', { ticker, quantity, side }),
  reset: () => post<{ status: string }>('/api/reset'),
  watchlist: () => request<{ items: WatchlistItem[] }>('/api/watchlist'),
  addTicker: (ticker: string) => post<{ ticker: string }>('/api/watchlist', { ticker }),
  removeTicker: (ticker: string) =>
    request<{ removed: string }>(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: 'DELETE' }),
  chat: (message: string) => post<ChatMessage>('/api/chat', { message }),
  chatHistory: () => request<{ messages: ChatMessage[] }>('/api/chat/history'),
};
