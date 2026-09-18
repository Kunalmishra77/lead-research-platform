/** Verifies every dev dependency is reachable and configured. Usage: pnpm infra:check */
import { HeadBucketCommand } from '@aws-sdk/client-s3';
import { Redis } from 'ioredis';
import postgres from 'postgres';

import { loadEnv, redact, required } from './env.ts';
import { bucketNames, s3Client } from './s3.ts';

loadEnv();

type Check = [name: string, run: () => Promise<string>];

const REQUIRED_EXTENSIONS = ['citext', 'pg_trgm', 'pgcrypto', 'postgis', 'vector'];

async function withDb<T>(url: string, fn: (sql: postgres.Sql) => Promise<T>): Promise<T> {
  const sql = postgres(url, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    onnotice: () => undefined,
  });
  try {
    return await fn(sql);
  } finally {
    await sql.end();
  }
}

async function checkOwner(): Promise<string> {
  return withDb(required('DATABASE_URL_MIGRATIONS'), async (sql) => {
    const [v] = await sql<{ v: string }[]>`select current_setting('server_version') as v`;
    const ext = await sql<{ extname: string; schema: string }[]>`
      select e.extname, n.nspname as schema
      from pg_extension e join pg_namespace n on n.oid = e.extnamespace`;
    const missing = REQUIRED_EXTENSIONS.filter(
      (e) => !ext.some((r) => r.extname === e && r.schema === 'extensions'),
    );
    if (missing.length > 0) {
      throw new Error(`missing from schema extensions: ${missing.join(', ')}`);
    }
    const [schema] = await sql`select 1 from pg_namespace where nspname = 'app'`;
    if (!schema) throw new Error('schema app missing: run pnpm infra:bootstrap');
    return `Postgres ${v?.v ?? '?'}, extensions ok, schema app ok`;
  });
}

async function checkAppRole(envVar: string, role: string): Promise<string> {
  const url = required(envVar);
  return withDb(url, async (sql) => {
    const [me] = await sql<{ user: string; bypass: boolean; path: string }[]>`
      select current_user as user,
             (select rolbypassrls from pg_roles where rolname = current_user) as bypass,
             current_setting('search_path') as path`;
    if (me?.user !== role) throw new Error(`connected as ${me?.user ?? '?'}, expected ${role}`);
    if (me.bypass) throw new Error(`${role} must not have BYPASSRLS`);
    const [tx] = await sql.begin(async (tsql) => {
      await tsql`select set_config('app.org_id', '00000000-0000-0000-0000-000000000001', true)`;
      return tsql<{ org: string }[]>`select current_setting('app.org_id', true) as org`;
    });
    if (tx?.org !== '00000000-0000-0000-0000-000000000001') throw new Error('SET LOCAL not kept');
    if (me.path.replaceAll(' ', '') !== 'app,extensions') {
      throw new Error(`${role} search_path is "${me.path}", expected "app, extensions"`);
    }
    for (const schema of ['auth', 'storage', 'vault']) {
      const [priv] = await sql<{ ok: boolean }[]>`
        select has_schema_privilege(${schema}, 'usage') as ok`;
      if (priv?.ok !== false) throw new Error(`${role} can access schema ${schema}`);
    }
    return `${redact(url)} as ${role}, no BYPASSRLS, search_path=${me.path}`;
  });
}

async function checkRedis(): Promise<string> {
  const redis = new Redis(required('REDIS_URL'), { lazyConnect: true, maxRetriesPerRequest: 1 });
  try {
    await redis.connect();
    const info = await redis.info('server');
    const version = /redis_version:(\S+)/.exec(info)?.[1] ?? '?';
    const id = await redis.xadd('infra:check', 'MAXLEN', '10', '*', 'ping', '1');
    if (id === null) throw new Error('XADD returned null');
    await redis.xdel('infra:check', id);
    return `Redis ${version}, streams ok`;
  } finally {
    redis.disconnect();
  }
}

async function checkStorage(): Promise<string> {
  const s3 = s3Client();
  for (const bucket of bucketNames()) {
    await s3.send(new HeadBucketCommand({ Bucket: bucket }));
  }
  return `buckets ${bucketNames().join(', ')} ok`;
}

async function checkJwks(): Promise<string> {
  const res = await fetch(required('SUPABASE_JWKS_URL'), { signal: AbortSignal.timeout(15_000) });
  if (!res.ok) throw new Error(`HTTP ${String(res.status)}`);
  const body = (await res.json()) as { keys?: { alg?: string }[] };
  const algs = (body.keys ?? []).map((k) => k.alg ?? '?');
  if (!algs.some((a) => a === 'ES256' || a === 'RS256')) {
    throw new Error(`no asymmetric signing key (found: ${algs.join(', ') || 'none'})`);
  }
  return `signing keys: ${algs.join(', ')}`;
}

const checks: Check[] = [
  ['db owner (migrations)', checkOwner],
  ['db app_api', () => checkAppRole('DATABASE_URL', 'app_api')],
  ['db app_worker', () => checkAppRole('DATABASE_URL_WORKERS', 'app_worker')],
  ['redis', checkRedis],
  ['storage (S3)', checkStorage],
  ['auth JWKS', checkJwks],
];

let failed = 0;
// Drivers can surface connection failures on background promises; report them instead of crashing.
process.on('unhandledRejection', (err) => {
  console.log(`FAIL  background error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
for (const [name, run] of checks) {
  try {
    console.log(`PASS  ${name.padEnd(22)} ${await run()}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL  ${name.padEnd(22)} ${err instanceof Error ? err.message : String(err)}`);
  }
}
process.exit(failed === 0 ? 0 : 1);
