import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { installDevDnsFromEnv } from '@leadforge/dev-dns';

/**
 * Loads the repo-root `.env` (without overriding already-set variables) and the dev-only DoH
 * resolver. The only place outside ConfigModule that touches process.env.
 */
export function loadEnvironment(): void {
  const file = resolve(__dirname, '..', '..', '..', '..', '.env');
  if (existsSync(file)) process.loadEnvFile(file);
  installDevDnsFromEnv(process.env);
}
