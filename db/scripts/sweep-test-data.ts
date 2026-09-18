/**
 * Removes data left behind by crashed live test runs: auth users with @test.invalid emails, the
 * orgs they own, orgs with test slugs, and their audit rows. Never touches real users.
 * Usage: pnpm db:sweep-test-data (owner role via DATABASE_URL_MIGRATIONS).
 */
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import postgres from 'postgres';

const envFile = resolve(import.meta.dirname, '..', '..', '.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

const url = process.env.DATABASE_URL_MIGRATIONS;
if (!url) throw new Error('DATABASE_URL_MIGRATIONS is required');

const sql = postgres(url, { max: 1, prepare: false });
try {
  const users = await sql<
    { id: string }[]
  >`select id from auth.users where email like '%@test.invalid'`;
  const ids = users.map((u) => u.id);
  const orgs = await sql`
    delete from app.organizations
    where slug like 'rls-%'
       or id in (select org_id from app.memberships where user_id = any(${ids}::uuid[]) and role = 'owner')
    returning id`;
  const audit =
    await sql`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[]) returning id`;
  await sql`delete from auth.users where id = any(${ids}::uuid[])`;
  console.log(
    `sweep: removed ${String(ids.length)} test users, ${String(orgs.length)} orgs, ${String(audit.length)} audit rows`,
  );
} finally {
  await sql.end();
}
