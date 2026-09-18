import { sql } from 'drizzle-orm';
import {
  bigint,
  boolean,
  check,
  foreignKey,
  index,
  jsonb,
  text,
  unique,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { membershipRole } from './enums.ts';

// Foreign keys to auth.users (Supabase-owned) are added in the custom RLS migration.

export const organizations = app.table(
  'organizations',
  {
    id: uuid('id').primaryKey(),
    name: text('name').notNull(),
    slug: text('slug').notNull(),
    plan: text('plan').notNull().default('free'),
    region: text('region').notNull().default('IN'),
    /** Cache of the credit ledger, updated in the same transaction as each ledger row. */
    creditsBalance: bigint('credits_balance', { mode: 'number' }).notNull().default(0),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    unique('organizations_slug_key').on(t.slug),
    check('organizations_slug_format', sql`${t.slug} ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'`),
    check('organizations_name_length', sql`char_length(${t.name}) between 1 and 120`),
    check('organizations_region_iso2', sql`${t.region} ~ '^[A-Z]{2}$'`),
  ],
);

export const workspaces = app.table(
  'workspaces',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    settings: jsonb('settings').notNull().default({}),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    index('workspaces_org_id_idx').on(t.orgId),
    // Target of composite FKs: child rows can only reference a workspace of their own org.
    unique('workspaces_id_org_key').on(t.id, t.orgId),
    check('workspaces_name_length', sql`char_length(${t.name}) between 1 and 120`),
  ],
);

export const memberships = app.table(
  'memberships',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    workspaceId: uuid('workspace_id').notNull(),
    userId: uuid('user_id').notNull(),
    role: membershipRole('role').notNull(),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    // Composite FK: a membership row in org A can never point at a workspace of org B.
    foreignKey({
      name: 'memberships_workspace_org_fk',
      columns: [t.workspaceId, t.orgId],
      foreignColumns: [workspaces.id, workspaces.orgId],
    }).onDelete('cascade'),
    unique('memberships_workspace_user_key').on(t.workspaceId, t.userId),
    index('memberships_user_id_idx').on(t.userId),
    index('memberships_org_id_idx').on(t.orgId),
  ],
);

/** 1:1 with auth.users. Not a tenant table: each user sees only their own row. */
export const userProfiles = app.table('user_profiles', {
  userId: uuid('user_id').primaryKey(),
  fullName: text('full_name'),
  isPlatformStaff: boolean('is_platform_staff').notNull().default(false),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});
