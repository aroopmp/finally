'use client';

import { useEffect, useRef, useState } from 'react';

export const FLASH_MS = 500;

/** Price text that flashes green/red for ~500ms when the value changes. */
export function FlashPrice({ price, text, testId }: { price: number | undefined; text: string; testId?: string }) {
  const prev = useRef(price);
  const [dir, setDir] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    if (price === undefined || prev.current === undefined || price === prev.current) {
      prev.current = price;
      return;
    }
    setDir(price > prev.current ? 'up' : 'down');
    prev.current = price;
    const id = setTimeout(() => setDir(null), FLASH_MS);
    return () => clearTimeout(id);
  }, [price]);

  const cls = dir === 'up' ? 'flash-up' : dir === 'down' ? 'flash-down' : '';
  return (
    <span data-testid={testId} data-flash={dir ?? ''} className={`price-cell num ${cls}`}>
      {text}
    </span>
  );
}
