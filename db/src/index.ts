import { sql } from 'drizzle-orm';
import { drizzle, type PostgresJsDatabase } from 'drizzle-orm/postgres-js';
import type postgres from 'postgres';

import * as billing from '../schema/billing.ts';
import * as enums from '../schema/enums.ts';
import * as partitioned from '../schema/partitioned.ts';
import * as research from '../schema/research.ts';
import * as sources from '../schema/sources.ts';
import * as tenancy from '../schema/tenancy.ts';

export * from '../schema/billing.ts';
export * from '../schema/enums.ts';
export * from '../schema/partitioned.ts';
export * from '../schema/research.ts';
export * from '../schema/sources.ts';
export * from '../schema/tenancy.ts';

// Consumers (the CommonJS API) must use Drizzle through this package: importing drizzle-orm directly
// there would load its CJS build next to this ESM build (incompatible types, dual-package hazard).
export { and, asc, desc, eq, inArray, isNull, ne, or, sql } from 'drizzle-orm';

export const schema = { ...enums, ...tenancy, ...sources, ...research, ...billing, ...partitioned };

export type Database = PostgresJsDatabase<typeof schema>;
export type Transaction = Parameters<Parameters<Database['transaction']>[0]>[0];

export function createDb(client: postgres.Sql): Database {
  return drizzle(client, { schema });
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function assertUuid(name: string, value: unknown): asserts value is string {
  if (typeof value !== 'string' || !UUID.test(value)) throw new TypeError(`${name} must be a UUID`);
}

/** Thrown when a user context names an org the user is not a member of. */
export class NotAMemberError extends Error {
  constructor() {
    super('user is not a member of the requested organization');
    this.name = 'NotAMemberError';
  }
}

export interface TenantContext {
  orgId: string;
  /** The acting user; omit for system work (workers) that has no user. */
  userId?: string | null;
}

/**
 * Runs `fn` in a transaction with the tenant context set transaction-locally
 * (`set_config(..., true)`), which is what RLS policies read. Safe with the transaction pooler.
 * Every query on a tenant table must go through this (CLAUDE.md tenant isolation).
 * When `userId` is given, membership in `orgId` is verified in the same transaction (defence in
 * depth behind the API's own check); throws NotAMemberError otherwise.
 */
export async function withTenant<T>(
  db: Database,
  context: TenantContext,
  fn: (tx: Transaction) => Promise<T>,
): Promise<T> {
  assertUuid('orgId', context.orgId);
  const userId = context.userId ?? null;
  if (userId !== null) assertUuid('userId', userId);
  return db.transaction(async (tx) => {
    await tx.execute(
      sql`select set_config('app.org_id', ${context.orgId}, true), set_config('app.user_id', ${userId ?? ''}, true)`,
    );
    // Separate statement: the context above must be in effect before RLS evaluates this query.
    if (userId !== null) {
      const rows = await tx.execute<{ member: boolean }>(
        sql`select exists (select 1 from app.memberships m
                           where m.org_id = ${context.orgId}::uuid and m.user_id = ${userId}::uuid) as member`,
      );
      if (rows[0]?.member !== true) throw new NotAMemberError();
    }
    return fn(tx);
  });
}

/**
 * User-only context (no active org): lets a user read their own profile and memberships, e.g. to
 * pick an org after login. Tenant rows stay invisible.
 */
export async function withUser<T>(
  db: Database,
  userId: string,
  fn: (tx: Transaction) => Promise<T>,
): Promise<T> {
  assertUuid('userId', userId);
  return db.transaction(async (tx) => {
    await tx.execute(
      sql`select set_config('app.org_id', '', true), set_config('app.user_id', ${userId}, true)`,
    );
    return fn(tx);
  });
}
