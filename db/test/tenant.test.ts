import postgres from 'postgres';
import { afterAll, describe, expect, it, vi } from 'vitest';

import { createDb, NotAMemberError, withTenant, withUser } from '../src/index.ts';

const ORG = '01923f4e-7b3a-7c2d-9f10-000000000001';
const USER = '01923f4e-7b3a-7c2d-9f10-0000000000aa';

describe('context validation (no database)', () => {
  // A client that is never connected: validation must fail before any query is sent.
  const db = createDb(postgres('postgres://unused@127.0.0.1:1/unused', { max: 1 }));

  it('rejects non-UUID org ids', async () => {
    await expect(
      withTenant(db, { orgId: "x'; drop table app.x; --" }, () => Promise.resolve(1)),
    ).rejects.toThrow(/orgId must be a UUID/);
  });

  it('rejects non-UUID user ids', async () => {
    await expect(
      withTenant(db, { orgId: ORG, userId: 'nope' }, () => Promise.resolve(1)),
    ).rejects.toThrow(/userId must be a UUID/);
    await expect(withUser(db, 'nope', () => Promise.resolve(1))).rejects.toThrow(
      /userId must be a UUID/,
    );
  });
});

const apiUrl = process.env.DATABASE_URL;

describe.skipIf(!apiUrl)('context against the database (app_api)', () => {
  const client = postgres(apiUrl ?? '', { max: 1, prepare: false });
  const db = createDb(client);

  afterAll(async () => {
    await client.end();
  });

  it('sets the org transaction-locally (system context) and clears it afterwards', async () => {
    const inside = await withTenant(db, { orgId: ORG }, async (tx) =>
      tx.execute<{ org: string; usr: string | null }>(
        'select app.current_org_id()::text as org, app.current_user_id()::text as usr',
      ),
    );
    expect(inside[0]).toEqual({ org: ORG, usr: null });
    const after = await client<{ org: string | null }[]>`select app.current_org_id()::text as org`;
    expect(after[0]?.org).toBeNull();
  });

  it('refuses a user context for an org the user does not belong to', async () => {
    const run = vi.fn(() => Promise.resolve('should not run'));
    await expect(withTenant(db, { orgId: ORG, userId: USER }, run)).rejects.toBeInstanceOf(
      NotAMemberError,
    );
    expect(run).not.toHaveBeenCalled();
  });

  it('withUser leaves the org unset', async () => {
    const rows = await withUser(db, USER, (tx) =>
      tx.execute<{ org: string | null; usr: string }>(
        'select app.current_org_id()::text as org, app.current_user_id()::text as usr',
      ),
    );
    expect(rows[0]).toEqual({ org: null, usr: USER });
  });
});
