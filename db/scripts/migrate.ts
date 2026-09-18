/** Applies pending migrations as the table owner. Usage: pnpm db:migrate (needs DATABASE_URL_MIGRATIONS). */
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

const envFile = resolve(import.meta.dirname, '..', '..', '.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

const url = process.env.DATABASE_URL_MIGRATIONS;
if (!url) throw new Error('DATABASE_URL_MIGRATIONS is required (see infra/setup/SETUP.md)');

const client = postgres(url, {
  max: 1,
  prepare: false,
  onnotice: (n) => {
    console.log(`notice: ${n.message}`);
  },
});
try {
  await migrate(drizzle(client), {
    migrationsFolder: resolve(import.meta.dirname, '..', 'migrations'),
    migrationsSchema: 'drizzle',
    migrationsTable: '__drizzle_migrations',
  });
  console.log('migrate: database is up to date');
} finally {
  await client.end();
}
