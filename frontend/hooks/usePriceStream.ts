'use client';

import { useEffect, useRef, useState } from 'react';
import type { ConnectionStatus, Point, PriceMap } from '@/lib/types';

export const MAX_HISTORY_POINTS = 1200;

/** Append a quote to a chart series: one point per whole second, strictly increasing. */
export function appendPoint(series: Point[], asOf: number, price: number): void {
  const time = Math.floor(asOf);
  const last = series[series.length - 1];
  if (!last || time > last.time) {
    series.push({ time, value: price });
    if (series.length > MAX_HISTORY_POINTS) series.shift();
  } else if (time === last.time) {
    last.value = price;
  } // older timestamps are ignored so charts never receive out-of-order data
}

export function statusFromReadyState(es: Pick<EventSource, 'readyState'>): ConnectionStatus {
  return es.readyState === 2 /* CLOSED */ ? 'disconnected' : 'reconnecting';
}

export interface PriceStream {
  prices: PriceMap;
  status: ConnectionStatus;
  /** Bounded per-ticker price history accumulated since page load. Mutated in place; read it on `version` changes. */
  history: React.MutableRefObject<Map<string, Point[]>>;
  version: number;
}

export function usePriceStream(url = '/api/stream/prices'): PriceStream {
  const [prices, setPrices] = useState<PriceMap>({});
  const [status, setStatus] = useState<ConnectionStatus>('reconnecting');
  const [version, setVersion] = useState(0);
  const history = useRef<Map<string, Point[]>>(new Map());

  useEffect(() => {
    const es = new EventSource(url);
    es.onopen = () => setStatus('connected');
    es.onerror = () => setStatus(statusFromReadyState(es));
    es.onmessage = (event: MessageEvent) => {
      let data: PriceMap;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      setStatus('connected');
      const store = history.current;
      for (const ticker of [...store.keys()]) if (!(ticker in data)) store.delete(ticker);
      for (const [ticker, quote] of Object.entries(data)) {
        let series = store.get(ticker);
        if (!series) store.set(ticker, (series = []));
        appendPoint(series, quote.as_of, quote.price);
      }
      setPrices(data);
      setVersion((v) => v + 1);
    };
    return () => es.close();
  }, [url]);

  return { prices, status, history, version };
}
