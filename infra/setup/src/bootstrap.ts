/** Applies sql/bootstrap.sql and sets app role passwords. Usage: pnpm infra:bootstrap */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import postgres from 'postgres';

import { loadEnv, redact, required } from './env.ts';
import { scramSha256Verifier } from './scram.ts';

loadEnv();

const url = required('DATABASE_URL_MIGRATIONS');
const passwords: Record<string, string> = {
  app_api: required('APP_API_DB_PASSWORD'),
  app_worker: required('APP_WORKER_DB_PASSWORD'),
};

for (const [role, password] of Object.entries(passwords)) {
  if (password.length < 16) throw new Error(`${role} password must be at least 16 characters`);
}

const sql = postgres(url, { max: 1, prepare: false, onnotice: () => undefined });
try {
  console.log(`bootstrap: connecting to ${redact(url)}`);
  await sql.unsafe(
    readFileSync(resolve(import.meta.dirname, '..', 'sql', 'bootstrap.sql'), 'utf8'),
  );
  for (const [role, password] of Object.entries(passwords)) {
    // ALTER ROLE cannot take bind parameters; format(%I, %L) quotes server-side. Only the SCRAM
    // verifier is sent, never the plaintext password. The salt is fixed per role so re-running
    // bootstrap yields the same verifier: the Supabase pooler caches credentials, and a changed
    // verifier forces "Authentication credentials are invalid" until its cache refreshes.
    const salt = createHash('sha256')
      .update(`leadforge-scram-salt:${role}`)
      .digest()
      .subarray(0, 16);
    const verifier = scramSha256Verifier(password, salt);
    const [row] = await sql<{ stmt: string }[]>`
      select format('alter role %I with password %L', ${role}::text, ${verifier}::text) as stmt`;
    if (!row) throw new Error('format() returned no row');
    await sql.unsafe(row.stmt);
  }
  console.log('bootstrap: schema app, extensions and roles app_api/app_worker are ready');
} finally {
  await sql.end();
}
