/**
 * Live e2e against the dev/test database (skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS).
 * Creates throwaway auth users with the owner role and removes everything it created.
 */
import { Controller, Get } from '@nestjs/common';
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { CurrentTenant } from '../src/common/current-request.decorator';
import { RequirePermission } from '../src/common/rbac/require-permission.decorator';
import type { TenantInfo } from '../src/modules/auth/auth.types';
import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

@Controller('__rbac')
class RbacProbeController {
  @Get('billing')
  @RequirePermission('billing.manage')
  billing(@CurrentTenant() tenant: TenantInfo): TenantInfo {
    return tenant;
  }

  @Get('contacts')
  @RequirePermission('contacts.view')
  contacts(@CurrentTenant() tenant: TenantInfo): TenantInfo {
    return tenant;
  }
}

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl && process.env.DATABASE_URL);

describe.skipIf(!live)('orgs + RBAC against the database', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false });
  const users = { alice: uuidv7(), bob: uuidv7(), unconfirmed: uuidv7() };
  const sessions: Record<string, string> = {
    [users.alice]: uuidv7(),
    [users.bob]: uuidv7(),
    [users.unconfirmed]: uuidv7(),
  };
  const createdOrgs: string[] = [];
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };
  const bearer = async (userId: string) => ({
    authorization: `Bearer ${await keys.sign({
      sub: userId,
      email: `${userId}@test.invalid`,
      session_id: sessions[userId],
    })}`,
  });

  beforeAll(async () => {
    keys = await createTestKeys();
    for (const [name, id] of Object.entries(users)) {
      await owner`
        insert into auth.users (id, email, aud, role, email_confirmed_at)
        values (${id}, ${`${name}-${id}@test.invalid`}, 'authenticated', 'authenticated',
                ${name === 'unconfirmed' ? null : new Date()})`;
      await owner`insert into auth.sessions (id, user_id) values (${sessions[id] ?? ''}, ${id})`;
    }
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true, controllers: [RbacProbeController] },
    );
  });

  afterAll(async () => {
    const ids = Object.values(users);
    try {
      await app?.close();
    } finally {
      // Orgs owned by the test users (even ones whose ids were never recorded), then the users.
      await owner`
        delete from app.organizations
        where id = any(${createdOrgs}::uuid[])
           or id in (select org_id from app.memberships where user_id = any(${ids}::uuid[]) and role = 'owner')`;
      await owner`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[])`;
      await owner`delete from auth.users where id = any(${ids}::uuid[])`;
      await owner.end();
    }
  });

  it('creates an org with default workspace and owner membership', async () => {
    const res = await api().inject({
      method: 'POST',
      url: '/app/orgs',
      headers: await bearer(users.alice),
      payload: { name: 'Acme Test Ltd' },
    });
    expect(res.statusCode, res.body).toBe(201);
    const body = res.json<{ org: { id: string; slug: string }; workspace: { id: string } }>();
    createdOrgs.push(body.org.id);
    expect(body.org.slug).toMatch(/^acme-test-ltd-[0-9a-f]{6}$/);

    const [audit] = await owner`
      select action from app.audit_logs where org_id = ${body.org.id} and actor_user_id = ${users.alice}`;
    expect(audit?.action).toBe('org.created');
  });

  it('GET /app/me lists only the caller memberships', async () => {
    const me = await api().inject({
      method: 'GET',
      url: '/app/me',
      headers: await bearer(users.alice),
    });
    expect(me.statusCode).toBe(200);
    const body = me.json<{ user: { id: string }; memberships: { role: string }[] }>();
    expect(body.user.id).toBe(users.alice);
    expect(body.memberships).toHaveLength(1);
    expect(body.memberships[0]?.role).toBe('owner');

    const bobMe = await api().inject({
      method: 'GET',
      url: '/app/me',
      headers: await bearer(users.bob),
    });
    expect(bobMe.json<{ memberships: unknown[] }>().memberships).toEqual([]);
  });

  it('rejects a taken slug with 409', async () => {
    const first = await api().inject({
      method: 'POST',
      url: '/app/orgs',
      headers: await bearer(users.bob),
      payload: { name: 'Bob Co', slug: `bob-${users.bob.slice(-8)}` },
    });
    expect(first.statusCode, first.body).toBe(201);
    createdOrgs.push(first.json<{ org: { id: string } }>().org.id);
    const dup = await api().inject({
      method: 'POST',
      url: '/app/orgs',
      headers: await bearer(users.alice),
      payload: { name: 'Other', slug: `bob-${users.bob.slice(-8)}` },
    });
    expect(dup.statusCode).toBe(409);
    expect(dup.json()).toMatchObject({ code: 'org.slug_taken' });
  });

  it('blocks users with an unconfirmed email from creating orgs', async () => {
    const res = await api().inject({
      method: 'POST',
      url: '/app/orgs',
      headers: await bearer(users.unconfirmed),
      payload: { name: 'Nope' },
    });
    expect(res.statusCode).toBe(403);
    expect(res.json()).toMatchObject({ code: 'auth.email_not_confirmed' });
  });

  it('validates the body', async () => {
    const res = await api().inject({
      method: 'POST',
      url: '/app/orgs',
      headers: await bearer(users.alice),
      payload: { name: '', slug: 'Bad Slug' },
    });
    expect(res.statusCode).toBe(400);
  });

  describe('RBAC guard', () => {
    let workspaceId: string;
    let orgId: string;

    beforeAll(async () => {
      const [row] = await owner<{ workspace_id: string; org_id: string }[]>`
        select workspace_id, org_id from app.memberships where user_id = ${users.alice} limit 1`;
      if (!row) throw new Error('alice has no membership');
      workspaceId = row.workspace_id;
      orgId = row.org_id;
      // Bob becomes a viewer in Alice's workspace.
      await owner`
        insert into app.memberships (id, org_id, workspace_id, user_id, role)
        values (${uuidv7()}, ${orgId}, ${workspaceId}, ${users.bob}, 'viewer')`;
    });

    const call = async (path: string, userId: string, workspace?: string) =>
      api().inject({
        method: 'GET',
        url: `/__rbac/${path}`,
        headers: {
          ...(await bearer(userId)),
          ...(workspace ? { 'x-workspace-id': workspace } : {}),
        },
      });

    it('owner passes and receives the resolved tenant', async () => {
      const res = await call('billing', users.alice, workspaceId);
      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ orgId, workspaceId, role: 'owner' });
    });

    it('viewer may view contacts but not manage billing', async () => {
      expect((await call('contacts', users.bob, workspaceId)).statusCode).toBe(200);
      const denied = await call('billing', users.bob, workspaceId);
      expect(denied.statusCode).toBe(403);
      expect(denied.json()).toMatchObject({ code: 'rbac.forbidden' });
    });

    it('non-members and unknown workspaces get the same 403', async () => {
      const outsider = await call('contacts', users.unconfirmed, workspaceId);
      const unknown = await call('contacts', users.alice, uuidv7());
      expect(outsider.statusCode).toBe(403);
      expect(unknown.statusCode).toBe(403);
      expect(outsider.json<{ code: string }>().code).toBe(unknown.json<{ code: string }>().code);
    });

    it('rejects a signed-out session even though its token is still valid', async () => {
      await owner`delete from auth.sessions where id = ${sessions[users.bob] ?? ''}`;
      const res = await call('contacts', users.bob, workspaceId);
      expect(res.statusCode).toBe(401);
      expect(res.json()).toMatchObject({ code: 'auth.session_revoked' });
      expect(res.headers['www-authenticate']).toBe('Bearer error="invalid_token"');
    });

    it('requires a valid workspace header', async () => {
      expect((await call('contacts', users.alice)).statusCode).toBe(400);
      expect((await call('contacts', users.alice, 'not-a-uuid')).statusCode).toBe(400);
    });
  });
});
