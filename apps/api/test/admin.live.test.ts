/**
 * Live e2e for the admin API against the dev/test database (skipped without DATABASE_URL +
 * DATABASE_URL_MIGRATIONS). Creates throwaway users with the owner role and removes them after.
 */
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl && process.env.DATABASE_URL);

interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

describe.skipIf(!live)('admin API against the database', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false });
  const users = { staff: uuidv7(), regular: uuidv7(), revoked: uuidv7() };
  const sessions: Record<string, string> = {
    [users.staff]: uuidv7(),
    [users.regular]: uuidv7(),
    [users.revoked]: uuidv7(),
  };
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };
  const get = async (url: string, userId?: string) =>
    api().inject({
      method: 'GET',
      url,
      headers: userId
        ? {
            authorization: `Bearer ${await keys.sign({
              sub: userId,
              email: `${userId}@test.invalid`,
              session_id: sessions[userId],
            })}`,
          }
        : {},
    });

  beforeAll(async () => {
    keys = await createTestKeys();
    for (const [name, id] of Object.entries(users)) {
      await owner`
        insert into auth.users (id, email, aud, role, email_confirmed_at)
        values (${id}, ${`${name}-${id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
      await owner`insert into auth.sessions (id, user_id) values (${sessions[id] ?? ''}, ${id})`;
      await owner`
        insert into app.user_profiles (user_id, is_platform_staff)
        values (${id}, ${name !== 'regular'})`;
    }
    await owner`delete from auth.sessions where id = ${sessions[users.revoked] ?? ''}`;
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true },
    );
  });

  afterAll(async () => {
    const ids = Object.values(users);
    try {
      await app?.close();
    } finally {
      await owner`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[])`;
      await owner`delete from app.user_profiles where user_id = any(${ids}::uuid[])`;
      await owner`delete from auth.users where id = any(${ids}::uuid[])`;
      await owner.end();
    }
  });

  it('lists users for platform staff, paginated by cursor, and audits each call', async () => {
    const first = await get('/admin/users?limit=1', users.staff);
    expect(first.statusCode, first.body).toBe(200);
    const page1 = first.json<Page<{ id: string; lastSignInAt: string | null }>>();
    expect(page1.items).toHaveLength(1);
    expect(page1.nextCursor).toBe(page1.items[0]?.id);

    const second = await get(`/admin/users?limit=1&cursor=${page1.nextCursor ?? ''}`, users.staff);
    const page2 = second.json<Page<{ id: string }>>();
    expect(second.statusCode).toBe(200);
    expect((page2.items[0]?.id ?? '') > (page1.items[0]?.id ?? '')).toBe(true);

    const audits = await owner<{ action: string; ip: string | null; user_agent: string | null }[]>`
      select action, host(ip) as ip, user_agent from app.audit_logs
      where actor_user_id = ${users.staff} and action = 'admin.users.listed'`;
    expect(audits).toHaveLength(2);
    expect(audits[0]?.ip).toBeTruthy(); // request origin recorded (migration 0011)
  });

  it('lists orgs for platform staff with cursor paging, audited', async () => {
    const res = await get('/admin/orgs?limit=1', users.staff);
    expect(res.statusCode, res.body).toBe(200);
    const body = res.json<Page<{ id: string; memberCount: number }>>();
    for (const org of body.items) expect(typeof org.memberCount).toBe('number');
    if (body.nextCursor) {
      const next = await get(`/admin/orgs?limit=1&cursor=${body.nextCursor}`, users.staff);
      const page2 = next.json<Page<{ id: string }>>();
      expect((page2.items[0]?.id ?? '') > body.nextCursor).toBe(true);
    }
    const audits = await owner`
      select 1 from app.audit_logs
      where actor_user_id = ${users.staff} and action = 'admin.orgs.listed'`;
    expect(audits.length).toBeGreaterThanOrEqual(1);
  });

  it('refuses non-staff with 403 and writes no audit row', async () => {
    for (const path of ['/admin/users', '/admin/orgs']) {
      const res = await get(path, users.regular);
      expect(res.statusCode, path).toBe(403);
      expect(res.json()).toMatchObject({ code: 'admin.forbidden' });
    }
    const audits = await owner`
      select 1 from app.audit_logs
      where actor_user_id = ${users.regular} and action like 'admin.%'`;
    expect(audits).toHaveLength(0);
  });

  it('refuses revoked sessions and anonymous callers', async () => {
    expect((await get('/admin/users', users.revoked)).statusCode).toBe(401);
    expect((await get('/admin/users')).statusCode).toBe(401);
  });

  it('validates the query', async () => {
    expect((await get('/admin/users?limit=0', users.staff)).statusCode).toBe(400);
    expect((await get('/admin/users?limit=500', users.staff)).statusCode).toBe(400);
    expect((await get('/admin/users?cursor=nope', users.staff)).statusCode).toBe(400);
  });
});
