import { sql } from 'drizzle-orm';
import {
  bigint,
  check,
  foreignKey,
  index,
  integer,
  jsonb,
  timestamp,
  unique,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { errorClass, researchDepth, researchJobStatus } from './enums.ts';
import { organizations, workspaces } from './tenancy.ts';

/** Minimal in Phase 1 (docs/04); searches + tasks arrive in Phase 2. */
export const researchJobs = app.table(
  'research_jobs',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    workspaceId: uuid('workspace_id').notNull(),
    searchId: uuid('search_id'),
    createdBy: uuid('created_by'),
    status: researchJobStatus('status').notNull().default('queued'),
    depth: researchDepth('depth').notNull().default('standard'),
    creditBudget: integer('credit_budget').notNull().default(0),
    creditsReserved: integer('credits_reserved').notNull().default(0),
    creditsUsed: integer('credits_used').notNull().default(0),
    costMicros: bigint('cost_micros', { mode: 'number' }).notNull().default(0),
    progress: jsonb('progress').notNull().default({}),
    errorClass: errorClass('error_class'),
    startedAt: timestamp('started_at', { withTimezone: true, mode: 'date' }),
    finishedAt: timestamp('finished_at', { withTimezone: true, mode: 'date' }),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    foreignKey({
      name: 'research_jobs_workspace_org_fk',
      columns: [t.workspaceId, t.orgId],
      foreignColumns: [workspaces.id, workspaces.orgId],
    }).onDelete('cascade'),
    // Target of composite FKs from usage tables.
    unique('research_jobs_id_org_key').on(t.id, t.orgId),
    index('research_jobs_org_status_idx').on(t.orgId, t.status),
    index('research_jobs_workspace_created_idx').on(t.workspaceId, t.createdAt.desc()),
    check(
      'research_jobs_credits_nonnegative',
      sql`${t.creditBudget} >= 0 and ${t.creditsReserved} >= 0 and ${t.creditsUsed} >= 0 and ${t.costMicros} >= 0`,
    ),
  ],
);
