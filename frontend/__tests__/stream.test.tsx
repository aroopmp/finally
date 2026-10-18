import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { appendPoint, statusFromReadyState, usePriceStream } from '@/hooks/usePriceStream';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CLOSED = 2;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  close() { this.closed = true; }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent); }
}

const quote = (ticker: string, price: number, as_of: number) => ({
  ticker, price, prev_price: price, prev_close: price, change: 0, direction: 'flat', day_change: 0, day_change_pct: 0, as_of, source: 'simulator',
});

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe('connection status', () => {
  it('starts reconnecting, becomes connected on open', () => {
    const { result } = renderHook(() => usePriceStream('/s'));
    expect(result.current.status).toBe('reconnecting');
    act(() => FakeEventSource.instances[0].onopen?.());
    expect(result.current.status).toBe('connected');
  });

  it('shows reconnecting on transient error and disconnected once closed', () => {
    const { result } = renderHook(() => usePriceStream('/s'));
    const es = FakeEventSource.instances[0];
    act(() => es.onopen?.());
    act(() => { es.readyState = 0; es.onerror?.(); });
    expect(result.current.status).toBe('reconnecting');
    act(() => { es.readyState = 2; es.onerror?.(); });
    expect(result.current.status).toBe('disconnected');
    act(() => es.emit({ AAPL: quote('AAPL', 1, 100) }));
    expect(result.current.status).toBe('connected');
  });

  it('maps ready states', () => {
    expect(statusFromReadyState({ readyState: 2 })).toBe('disconnected');
    expect(statusFromReadyState({ readyState: 0 })).toBe('reconnecting');
  });

  it('closes the EventSource on unmount', () => {
    const { unmount } = renderHook(() => usePriceStream('/s'));
    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });
});

describe('price stream data', () => {
  it('stores prices and accumulates history, dropping removed tickers', () => {
    const { result } = renderHook(() => usePriceStream('/s'));
    const es = FakeEventSource.instances[0];
    act(() => es.emit({ AAPL: quote('AAPL', 100, 1000.2), MSFT: quote('MSFT', 400, 1000.2) }));
    act(() => es.emit({ AAPL: quote('AAPL', 101, 1001.4) }));
    expect(result.current.prices.AAPL.price).toBe(101);
    expect(result.current.history.current.get('AAPL')).toEqual([{ time: 1000, value: 100 }, { time: 1001, value: 101 }]);
    expect(result.current.history.current.has('MSFT')).toBe(false);
  });

  it('ignores malformed payloads', () => {
    const { result } = renderHook(() => usePriceStream('/s'));
    act(() => FakeEventSource.instances[0].onmessage?.({ data: 'not json' } as MessageEvent));
    expect(result.current.prices).toEqual({});
  });
});

describe('appendPoint', () => {
  it('keeps strictly increasing whole-second times', () => {
    const s: { time: number; value: number }[] = [];
    appendPoint(s, 10.1, 1);
    appendPoint(s, 10.9, 2); // same second: replaces
    appendPoint(s, 9.0, 3); // older: ignored
    appendPoint(s, 11.0, 4);
    expect(s).toEqual([{ time: 10, value: 2 }, { time: 11, value: 4 }]);
  });

  it('bounds the history length', () => {
    const s: { time: number; value: number }[] = [];
    for (let i = 0; i < 1500; i++) appendPoint(s, i, i);
    expect(s.length).toBe(1200);
    expect(s[s.length - 1].time).toBe(1499);
  });
});
