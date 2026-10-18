import { test, expect } from '@playwright/test';

const DEFAULTS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

test.beforeEach(async ({ page, request }) => {
  await request.post('/api/reset');
  await page.goto('/');
  // The trade bar auto-fills the selected ticker once hydrated and the watchlist has loaded;
  // typing before then is overwritten, so wait for it.
  await expect(page.getByTestId('trade-ticker')).toHaveValue('AAPL');
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

const money = (s: string | null) => Number((s ?? '').replace(/[$,]/g, ''));

test('prices are streaming (values change)', async ({ page }) => {
  const el = page.getByTestId('price-AAPL');
  await expect(el).toHaveText(/\d/);
  const first = await el.textContent();
  await expect.poll(async () => el.textContent(), { timeout: 15_000 }).not.toBe(first);
});

test('buy decreases cash by roughly qty*price; partial sell keeps position', async ({ page }) => {
  await page.getByTestId('trade-ticker').fill('AAPL');
  await page.getByTestId('trade-qty').fill('10');
  await page.getByTestId('trade-buy').click();
  await expect(page.getByTestId('position-qty-AAPL')).toHaveText(/^10(\.0+)?$/);
  const cash = money(await page.getByTestId('header-cash').textContent());
  expect(cash).toBeLessThan(10000);
  expect(cash).toBeGreaterThan(0);

  await page.getByTestId('trade-qty').fill('4');
  await page.getByTestId('trade-sell').click();
  await expect(page.getByTestId('position-qty-AAPL')).toHaveText(/^6(\.0+)?$/);
  await expect.poll(async () => money(await page.getByTestId('header-cash').textContent())).toBeGreaterThan(cash);
});

test('rejected trades do not change state', async ({ page, request }) => {
  await page.getByTestId('trade-ticker').fill('AAPL');
  await page.getByTestId('trade-qty').fill('1');
  await page.getByTestId('trade-sell').click(); // nothing owned
  await expect(page.getByTestId('trade-notice')).not.toBeEmpty();
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');

  const r = await request.post('/api/portfolio/trade', { data: { ticker: 'AAPL', quantity: 1e9, side: 'buy' } });
  expect(r.status()).toBe(400);
  const bad = await request.post('/api/portfolio/trade', { data: { ticker: 'ZZZZZ', quantity: 1, side: 'buy' } });
  expect(bad.status()).toBe(400);
});

test('state persists across reload', async ({ page }) => {
  await page.getByTestId('trade-ticker').fill('V');
  await page.getByTestId('trade-qty').fill('2');
  await page.getByTestId('trade-buy').click();
  await expect(page.getByTestId('position-row-V')).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('position-row-V')).toBeVisible();
});

test('mock chat: watchlist change, canned reply, history restored', async ({ page }) => {
  await page.getByTestId('chat-input').fill('add PYPL');
  await page.getByTestId('chat-send').click();
  await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();

  await page.getByTestId('chat-input').fill('hello there');
  await page.getByTestId('chat-send').click();
  await expect(page.getByTestId('chat-assistant')).toHaveCount(2);

  await page.reload();
  await expect(page.getByTestId('chat-user')).toHaveCount(2);
});

test('mock chat: failed trade reported per action', async ({ page }) => {
  await page.getByTestId('chat-input').fill('sell 5 AAPL');
  await page.getByTestId('chat-send').click();
  await expect(page.getByTestId('chat-action').first()).toHaveAttribute('data-status', 'error');
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
});

test('API: health, portfolio, history, reset', async ({ request }) => {
  expect((await request.get('/api/health')).ok()).toBeTruthy();
  await request.post('/api/portfolio/trade', { data: { ticker: 'MSFT', quantity: 1, side: 'buy' } });
  const p = await (await request.get('/api/portfolio')).json();
  expect(p.positions.length).toBe(1);
  expect((await request.get('/api/portfolio/history')).ok()).toBeTruthy();
  await request.post('/api/reset');
  const p2 = await (await request.get('/api/portfolio')).json();
  expect(p2.positions.length).toBe(0);
  const w = await (await request.get('/api/watchlist')).json();
  expect(JSON.stringify(w)).toContain('NFLX');
});
