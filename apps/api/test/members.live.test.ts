/**
 * Live e2e (task 1.13): team directory, audited role changes and login (session) audit.
 * Skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS; removes everything it creates.
 */
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { MembersService } from '../src/modules/members/members.service';
import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl && process.env.DATABASE_URL);

type Label = 'owner' | 'admin' | 'member' | 'viewer' | 'outsider';

describe.skipIf(!live)('members + audit against the database', () => {
  const db = postgres(ownerUrl ?? '', { max: 1, prepare: false });
  const org = uuidv7();
  const workspace = uuidv7();
  const people: Record<Label, { id: string; session: string }> = {
    owner: { id: uuidv7(), session: uuidv7() },
    admin: { id: uuidv7(), session: uuidv7() },
    member: { id: uuidv7(), session: uuidv7() },
    viewer: { id: uuidv7(), session: uuidv7() },
    outsider: { id: uuidv7(), session: uuidv7() },
  };
  const ids = Object.values(people).map((p) => p.id);
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };
  const headers = async (who: Label) => ({
    authorization: `Bearer ${await keys.sign({ sub: people[who].id, session_id: people[who].session })}`,
    'x-workspace-id': workspace,
    'user-agent': 'members-live-test',
  });
  const patchRole = async (who: Label, target: string, role: string) =>
    api().inject({
      method: 'PATCH',
      url: `/app/members/${target}`,
      headers: await headers(who),
      payload: { role },
    });

  beforeAll(async () => {
    keys = await createTestKeys();
    for (const [label, p] of Object.entries(people)) {
      await db`insert into auth.users (id, email, aud, role, email_confirmed_at)
               values (${p.id}, ${`${label}-${p.id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
      await db`insert into auth.sessions (id, user_id) values (${p.session}, ${p.id})`;
    }
    await db`insert into app.organizations (id, name, slug) values (${org}, 'Members Test', ${`rls-members-${org.slice(-12)}`})`;
    await db`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    for (const label of ['owner', 'admin', 'member', 'viewer'] as const) {
      await db`insert into app.memberships (id, org_id, workspace_id, user_id, role)
               values (${uuidv7()}, ${org}, ${workspace}, ${people[label].id}, ${label}::app.membership_role)`;
    }
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true },
    );
  });

  afterAll(async () => {
    try {
      await app?.close();
    } finally {
      await db`delete from app.organizations where id = ${org}`;
      await db`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[]) or org_id = ${org}`;
      await db`delete from auth.users where id = any(${ids}::uuid[])`;
      await db.end();
    }
  });

  it('every member can read the team directory with emails', async () => {
    const res = await api().inject({
      method: 'GET',
      url: '/app/members',
      headers: await headers('viewer'),
    });
    expect(res.statusCode, res.body).toBe(200);
    const members = res.json<{ userId: string; email: string; role: string }[]>();
    expect(members.map((m) => m.role).sort()).toEqual(['admin', 'member', 'owner', 'viewer']);
    expect(members.every((m) => m.email.endsWith('@test.invalid'))).toBe(true);
  });

  it('outsiders cannot read the directory', async () => {
    const res = await api().inject({
      method: 'GET',
      url: '/app/members',
      headers: await headers('outsider'),
    });
    expect(res.statusCode).toBe(403);
  });

  it('admin changes a member role and the change is audited', async () => {
    const res = await patchRole('admin', people.member.id, 'manager');
    expect(res.statusCode, res.body).toBe(200);
    expect(res.json()).toEqual({ userId: people.member.id, role: 'manager' });
    const [row] = await db<{ actor: string; meta: { from: string; to: string }; ua: string }[]>`
      select actor_user_id::text as actor, meta, user_agent as ua from app.audit_logs
      where org_id = ${org} and action = 'membership.role_changed' and target_id = ${people.member.id}`;
    expect(row).toMatchObject({
      actor: people.admin.id,
      meta: { from: 'member', to: 'manager' },
      ua: 'members-live-test',
    });
  });

  it('only owners may grant or remove the owner role', async () => {
    expect((await patchRole('admin', people.member.id, 'owner')).json()).toMatchObject({
      code: 'member.owner_only',
    });
    expect((await patchRole('admin', people.owner.id, 'viewer')).statusCode).toBe(403);
  });

  it('the last owner cannot be demoted, a second owner makes it possible', async () => {
    const blocked = await patchRole('owner', people.owner.id, 'admin');
    expect(blocked.statusCode).toBe(409);
    expect(blocked.json()).toMatchObject({ code: 'member.last_owner' });
    expect((await patchRole('owner', people.admin.id, 'owner')).statusCode).toBe(200);
    expect((await patchRole('owner', people.owner.id, 'admin')).statusCode).toBe(200);
  });

  it('non-members are 404, viewers are forbidden, bad roles are 400', async () => {
    expect((await patchRole('admin', people.outsider.id, 'member')).statusCode).toBe(404);
    const viewer = await patchRole('viewer', people.member.id, 'viewer');
    expect(viewer.statusCode).toBe(403);
    expect(viewer.json()).toMatchObject({ code: 'rbac.forbidden' });
    expect((await patchRole('admin', people.member.id, 'superuser')).statusCode).toBe(400);
  });

  it('an admin cannot promote themselves to owner but may step down', async () => {
    // After the previous test the "owner" person is an admin.
    const promote = await patchRole('owner', people.owner.id, 'owner');
    expect(promote.statusCode).toBe(403);
    expect(promote.json()).toMatchObject({ code: 'member.owner_only' });
    expect((await patchRole('owner', people.owner.id, 'member')).statusCode).toBe(200);
  });

  it('re-checks the actor role under the lock (a stale guard role is not trusted)', async () => {
    const members = api().get(MembersService);
    // The viewer arrives with a stale TenantInfo claiming "owner" (e.g. demoted mid-request).
    await expect(
      members.changeRole(
        { userId: people.viewer.id, email: null, sessionId: people.viewer.session },
        { orgId: org, workspaceId: workspace, role: 'owner' },
        people.member.id,
        'viewer',
        { ip: null, userAgent: null },
      ),
    ).rejects.toMatchObject({ code: 'rbac.forbidden' });
  });

  it('app.workspace_members refuses non-members and wrong-org contexts at the SQL level', async () => {
    const apiDb = postgres(process.env.DATABASE_URL ?? '', { max: 1, prepare: false });
    const directory = (orgId: string, userId: string) =>
      apiDb.begin(async (tx) => {
        await tx`select set_config('app.org_id', ${orgId}, true), set_config('app.user_id', ${userId}, true)`;
        return tx`select user_id from app.workspace_members(${workspace}::uuid)`;
      });
    try {
      await expect(directory(org, people.outsider.id)).rejects.toThrow(/not a member/);
      await expect(directory(uuidv7(), people.viewer.id)).rejects.toThrow(/not a member/);
      expect(await directory(org, people.viewer.id)).toHaveLength(4);
    } finally {
      await apiDb.end();
    }
  });

  it('records exactly one login (session_started) audit row per session', async () => {
    // Several authenticated requests for the same session...
    for (let i = 0; i < 3; i += 1) {
      await api().inject({ method: 'GET', url: '/app/me', headers: await headers('member') });
    }
    // ...the audit write is detached from the request, so wait for it.
    const deadline = Date.now() + 5000;
    let rows: { actor: string; org: string | null; ip: string | null }[] = [];
    while (Date.now() < deadline) {
      rows = await db<{ actor: string; org: string | null; ip: string | null }[]>`
        select actor_user_id::text as actor, org_id::text as org, host(ip) as ip from app.audit_logs
        where action = 'auth.session_started' and actor_user_id = any(${ids}::uuid[])`;
      if (rows.some((r) => r.actor === people.member.id)) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const perUser = new Map<string, number>();
    for (const r of rows) perUser.set(r.actor, (perUser.get(r.actor) ?? 0) + 1);
    for (const who of ['owner', 'admin', 'member', 'viewer', 'outsider'] as const) {
      expect(perUser.get(people[who].id), who).toBe(1);
    }
    expect(rows.every((r) => r.org === null && r.ip !== null)).toBe(true);
  });
});
