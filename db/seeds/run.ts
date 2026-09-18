/** Idempotent seeds. Usage: pnpm db:seed (runs as the owner role). */
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';

import { SOURCE_SEEDS } from './sources.ts';

const envFile = resolve(import.meta.dirname, '..', '..', '.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

const url = process.env.DATABASE_URL_MIGRATIONS;
if (!url) throw new Error('DATABASE_URL_MIGRATIONS is required (see infra/setup/SETUP.md)');

const sql = postgres(url, { max: 1, prepare: false });
try {
  for (const s of SOURCE_SEEDS) {
    // Policy flags (enabled, legal_approved) are admin decisions and never overwritten by seeds.
    await sql`
      insert into app.sources (id, key, name, type, tos_class, default_ttl_days)
      values (${uuidv7()}, ${s.key}, ${s.name}, ${s.type}, ${s.tosClass}, ${s.defaultTtlDays})
      on conflict (key) do update
        set name = excluded.name, type = excluded.type, tos_class = excluded.tos_class,
            default_ttl_days = excluded.default_ttl_days`;
  }
  console.log(`seed: ${String(SOURCE_SEEDS.length)} sources upserted`);
} finally {
  await sql.end();
}
