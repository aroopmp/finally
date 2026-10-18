export type ConnectionStatus = 'connected' | 'reconnecting' | 'disconnected';

/** One point on a time-series chart (time = whole Unix seconds). */
export interface Point {
  time: number;
  value: number;
}

/** SSE quote payload (Unix seconds in `as_of`). */
export interface Quote {
  ticker: string;
  price: number;
  prev_price: number;
  prev_close: number | null;
  change: number;
  direction: 'up' | 'down' | 'flat';
  day_change: number;
  day_change_pct: number;
  as_of: number;
  source: string;
}

export type PriceMap = Record<string, Quote>;

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  /** null while no quote is available; value then falls back to cost basis. */
  price: number | null;
  market_value: number;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  day_change_pct: number | null;
  stale: boolean;
}

export interface Portfolio {
  cash_balance: number;
  positions: Position[];
  positions_value: number;
  total_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  prices_complete: boolean;
  market: { source: string; status: string };
}

export interface WatchlistItem {
  ticker: string;
  added_at: string;
  quote: Quote | null;
}

export interface Snapshot {
  total_value: number;
  recorded_at: string;
}

export interface Trade {
  id: string;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  total: number;
  executed_at: string;
}

export interface TradeResult {
  trade: Trade;
  cash_balance: number;
  position: Position | null;
}

export interface ActionError {
  code: string;
  message: string;
}

export type ChatAction =
  | { type: 'trade'; ticker: string; side: 'buy' | 'sell'; quantity: number; status: 'ok'; price: number; total: number; trade_id?: string }
  | { type: 'trade'; ticker: string; side: 'buy' | 'sell'; quantity: number; status: 'error'; error: ActionError }
  | { type: 'watchlist'; ticker: string; action: 'add' | 'remove'; status: 'ok' }
  | { type: 'watchlist'; ticker: string; action: 'add' | 'remove'; status: 'error'; error: ActionError };

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  actions: ChatAction[] | null;
  created_at: string;
  /** Client-only: marks a locally generated error bubble (never stored server-side). */
  error?: boolean;
}
