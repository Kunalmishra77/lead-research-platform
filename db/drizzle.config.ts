import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { defineConfig } from 'drizzle-kit';

// drizzle-kit bundles this file as CJS (no import.meta.dirname); its scripts run from db/.
const envFile = resolve(process.cwd(), '..', '.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

export default defineConfig({
  dialect: 'postgresql',
  // partitioned.ts is intentionally excluded: those tables are created by custom SQL migrations.
  schema: [
    './schema/enums.ts',
    './schema/tenancy.ts',
    './schema/sources.ts',
    './schema/graph.ts',
    './schema/research.ts',
    './schema/leads.ts',
    './schema/billing.ts',
    './schema/jobs.ts',
  ],
  out: './migrations',
  schemaFilter: ['app'],
  // Migration bookkeeping stays outside schema `app`, which the app roles can read.
  migrations: { schema: 'drizzle', table: '__drizzle_migrations' },
  dbCredentials: { url: process.env.DATABASE_URL_MIGRATIONS ?? '' },
  strict: true,
  verbose: true,
});
