import { defineConfig } from '@playwright/test';

// Run against the app container started with LLM_MOCK=true (scripts/start_mac.sh with .env LLM_MOCK=true).
export default defineConfig({
  testDir: './tests',
  workers: 1,
  use: { baseURL: process.env.BASE_URL ?? 'http://localhost:8000' },
});
