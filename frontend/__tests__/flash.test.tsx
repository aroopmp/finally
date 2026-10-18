import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FlashPrice } from '@/components/FlashPrice';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const flash = () => screen.getByTestId('p').getAttribute('data-flash');

describe('FlashPrice', () => {
  it('does not flash on first render', () => {
    render(<FlashPrice price={100} text="100.00" testId="p" />);
    expect(flash()).toBe('');
  });

  it('flashes up then clears after 500ms', () => {
    const { rerender } = render(<FlashPrice price={100} text="100.00" testId="p" />);
    rerender(<FlashPrice price={101} text="101.00" testId="p" />);
    expect(flash()).toBe('up');
    expect(screen.getByTestId('p')).toHaveClass('flash-up');
    act(() => { vi.advanceTimersByTime(499); });
    expect(flash()).toBe('up');
    act(() => { vi.advanceTimersByTime(2); });
    expect(flash()).toBe('');
  });

  it('flashes down on a downtick', () => {
    const { rerender } = render(<FlashPrice price={100} text="100.00" testId="p" />);
    rerender(<FlashPrice price={99} text="99.00" testId="p" />);
    expect(flash()).toBe('down');
  });

  it('does not flash when the price is unchanged', () => {
    const { rerender } = render(<FlashPrice price={100} text="100.00" testId="p" />);
    rerender(<FlashPrice price={100} text="100.00" testId="p" />);
    expect(flash()).toBe('');
  });
});
