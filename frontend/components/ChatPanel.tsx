'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { fmtQty, fmtUsd } from '@/lib/format';
import type { ChatAction, ChatMessage } from '@/lib/types';
import { useTrading } from './TradingProvider';

export function describeAction(a: ChatAction): { ok: boolean; text: string } {
  const ok = a.status === 'ok';
  if (a.type === 'trade') {
    const verb = a.side === 'buy' ? 'Bought' : 'Sold';
    if (a.status === 'ok') return { ok, text: `${verb} ${fmtQty(a.quantity)} ${a.ticker} @ ${fmtUsd(a.price)}` };
    return { ok, text: `${a.side === 'buy' ? 'Buy' : 'Sell'} ${fmtQty(a.quantity)} ${a.ticker} failed: ${a.error.message}` };
  }
  const verb = a.action === 'add' ? 'Added' : 'Removed';
  if (a.status === 'ok') return { ok, text: `${verb} ${a.ticker} ${a.action === 'add' ? 'to' : 'from'} watchlist` };
  return { ok, text: `${a.action === 'add' ? 'Add' : 'Remove'} ${a.ticker} failed: ${a.error.message}` };
}

export function ChatBubble({ m }: { m: ChatMessage }) {
  const mine = m.role === 'user';
  return (
    <div className={`flex flex-col ${mine ? 'items-end' : 'items-start'}`} data-testid={`chat-${m.role}`}>
      <div
        className={`max-w-[92%] whitespace-pre-wrap rounded-lg px-3 py-2 ${
          mine ? 'bg-primary/20 text-text' : m.error ? 'border border-down/50 bg-down/10 text-down' : 'bg-panel2 text-text'
        }`}
      >
        {m.content}
      </div>
      {m.actions && m.actions.length > 0 && (
        <ul className="mt-1 max-w-[92%] space-y-1">
          {m.actions.map((a, i) => {
            const d = describeAction(a);
            return (
              <li
                key={i}
                data-testid="chat-action"
                data-status={a.status}
                className={`rounded border px-2 py-1 text-xs ${d.ok ? 'border-up/40 bg-up/10 text-up' : 'border-down/40 bg-down/10 text-down'}`}
              >
                {d.ok ? '✓ ' : '✗ '}{d.text}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function ChatPanel() {
  const { refreshAll, resetCount } = useTrading();
  const [collapsed, setCollapsed] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    api.chatHistory().then((r) => setMessages(r.messages)).catch(() => {});
  }, [resetCount]);

  useEffect(() => { bottom.current?.scrollIntoView?.({ block: 'end' }); }, [messages, loading]);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: 'user', content: text, actions: null, created_at: '' }]);
    setLoading(true);
    try {
      const reply = await api.chat(text);
      setMessages((m) => [...m, reply]);
    } catch (err) {
      // Failed exchanges are not stored server-side; show a plain error bubble only.
      setMessages((m) => [...m, { id: `e-${Date.now()}`, role: 'assistant', content: (err as Error).message, actions: null, created_at: '', error: true }]);
    } finally {
      setLoading(false);
      void refreshAll();
    }
  };

  if (collapsed) {
    return (
      <button onClick={() => setCollapsed(false)} data-testid="chat-expand" className="panel flex w-9 items-start justify-center py-3 text-muted hover:text-text" aria-label="Open chat">
        <span className="[writing-mode:vertical-rl]">AI Assistant ‹</span>
      </button>
    );
  }

  return (
    <aside className="panel flex h-full min-h-0 w-full flex-col lg:w-[340px]" aria-label="AI assistant">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="panel-title">AI Assistant</span>
        <button onClick={() => setCollapsed(true)} aria-label="Collapse chat" className="text-muted hover:text-text">›</button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" data-testid="chat-log">
        {messages.length === 0 && !loading && (
          <p className="text-muted">Ask about your portfolio, or tell me to trade — e.g. “buy 10 AAPL” or “add PYPL to my watchlist”.</p>
        )}
        {messages.map((m) => <ChatBubble key={m.id} m={m} />)}
        {loading && <div data-testid="chat-loading" className="animate-pulse text-muted">FinAlly is thinking…</div>}
        <div ref={bottom} />
      </div>
      <form onSubmit={send} className="flex gap-2 border-t border-line p-2">
        <input
          data-testid="chat-input" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="Message FinAlly…" aria-label="Message"
          className="w-full rounded border border-line bg-bg px-2 py-1.5 outline-none focus:border-primary"
        />
        <button data-testid="chat-send" disabled={loading || !input.trim()} className="rounded bg-secondary px-3 py-1.5 font-semibold text-white hover:opacity-90 disabled:opacity-50">Send</button>
      </form>
    </aside>
  );
}
