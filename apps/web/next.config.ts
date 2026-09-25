import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';

import type { NextConfig } from 'next';

// One repo-root .env for every service (infra/setup/env.template). The web server only takes the
// keys it needs (never DB or storage credentials), and values already set in the environment win,
// so CI and the Playwright placeholders are never overridden.
const WEB_KEYS = [
  'APP_URL',
  'API_URL',
  'SUPABASE_URL',
  'SUPABASE_PUBLISHABLE_KEY',
  'DEV_DNS_OVER_HTTPS',
  'SENTRY_DSN_WEB',
  'SENTRY_ENVIRONMENT',
  'SENTRY_TRACES_SAMPLE_RATE',
];
const rootEnv = fileURLToPath(new URL('../../.env', import.meta.url));
if (existsSync(rootEnv)) {
  const values = parseEnv(readFileSync(rootEnv, 'utf8'));
  for (const key of WEB_KEYS) {
    if (process.env[key] === undefined && values[key] !== undefined) process.env[key] = values[key];
  }
}

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Emits `.next/standalone`: a server plus only the files it imports. The deploy image does not
  // use it yet — it runs `next start` from the built workspace, because that has fewer ways to
  // fail on a first deploy — but this is what it slims down to once the stack is proven.
  output: 'standalone',
  // Workspace packages are published as compiled ESM (dist/); nothing to transpile.
  headers: () => Promise.resolve([{ source: '/:path*', headers: securityHeaders }]),
  // A Sentry DSN is public by design; only the browser copy is inlined into client bundles. It is
  // baked in at build time, so production builds must set SENTRY_DSN_WEB explicitly (a local
  // .env value would otherwise ship).
  env: {
    NEXT_PUBLIC_SENTRY_DSN: process.env.SENTRY_DSN_WEB ?? '',
    NEXT_PUBLIC_SENTRY_ENVIRONMENT: process.env.SENTRY_ENVIRONMENT ?? '',
  },
};

export default config;
