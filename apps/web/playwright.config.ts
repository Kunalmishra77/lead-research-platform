import { defineConfig, devices } from '@playwright/test';

const PORT = 3100;

/** Smoke tests against a production build (`pnpm build` first). No live Supabase calls. */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: `http://localhost:${String(PORT)}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `pnpm exec next start --port ${String(PORT)}`,
    port: PORT,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      NODE_ENV: 'production',
      APP_URL: `http://localhost:${String(PORT)}`,
      API_URL: 'http://127.0.0.1:9',
      SUPABASE_URL: 'https://example.supabase.co',
      SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_e2e_placeholder_key',
    },
  },
});
