import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { installDevDnsFromEnv } from '@leadforge/dev-dns';

export const repoRoot = resolve(import.meta.dirname, '..', '..', '..');

/** Loads the repo-root .env (if present) without overriding variables already set, then dev DNS. */
export function loadEnv(): void {
  const file = resolve(repoRoot, '.env');
  if (existsSync(file)) process.loadEnvFile(file);
  installDevDnsFromEnv();
}

export function required(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === '') {
    throw new Error(`Missing required env var ${name} (see infra/setup/SETUP.md)`);
  }
  return value;
}

/** Hides credentials when printing connection strings. */
export function redact(url: string): string {
  return url.replace(/\/\/([^:/@]+):[^@]*@/, '//$1:***@');
}
