import { test, expect } from '@playwright/test';

const DEFAULTS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

test.beforeEach(async ({ page, request }) => {
  await request.post('/api/reset');
  await page.goto('/');
});

test('fresh start: watchlist, $10k, streaming', async ({ page }) => {
  for (const t of DEFAULTS) await expect(page.getByTestId(`watchlist-row-${t}`)).toBeVisible();
  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');
  await expect(page.getByTestId('conn-dot')).toHaveAttribute('data-status', 'connected');
});

test('add and remove a ticker', async ({ page }) => {
  await page.getByTestId('watchlist-add-input').fill('PYPL');
  await page.getByTestId('watchlist-add').click();
  await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();
  await page.getByTestId('watchlist-remove-PYPL').click();
  await expect(page.getByTestId('watchlist-row-PYPL')).toHaveCount(0);
});

test('buy then sell', async ({ page }) => {
  await page.getByTestId('trade-ticker').fill('AAPL');
  await page.getByTestId('trade-qty').fill('5');
  await page.getByTestId('trade-buy').click();
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await expect(page.getByTestId('header-cash')).not.toHaveText('$10,000.00');
  await page.getByTestId('trade-sell').click();
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
});

test('portfolio visualisation', async ({ page }) => {
  await page.getByTestId('trade-ticker').fill('MSFT');
  await page.getByTestId('trade-qty').fill('3');
  await page.getByTestId('trade-buy').click();
  await expect(page.getByTestId('heat-MSFT')).toBeVisible();
  await expect(page.getByTestId('pnl-chart')).toHaveAttribute('data-points', /^[1-9]/);
});

test('mocked AI chat executes a trade', async ({ page }) => {
  await page.getByTestId('chat-input').fill('buy 2 NVDA');
  await page.getByTestId('chat-send').click();
  await expect(page.getByTestId('chat-action').first()).toBeVisible();
  await expect(page.getByTestId('position-row-NVDA')).toBeVisible();
});
