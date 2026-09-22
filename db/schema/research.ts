import { sql } from 'drizzle-orm';
import {
  bigint,
  check,
  foreignKey,
  index,
  integer,
  jsonb,
  smallint,
  text,
  timestamp,
  unique,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { errorClass, researchDepth, researchJobStatus, researchTaskStatus } from './enums.ts';
import { organizations, workspaces } from './tenancy.ts';

/** A user's research request: the raw prompt and the validated ResearchSpec it compiled to. */
export const searches = app.table(
  'searches',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    workspaceId: uuid('workspace_id').notNull(),
    userId: uuid('user_id'),
    rawQuery: text('raw_query').notNull(),
    spec: jsonb('spec').notNull(),
    specVersion: smallint('spec_version').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    foreignKey({
      name: 'searches_workspace_org_fk',
      columns: [t.workspaceId, t.orgId],
      foreignColumns: [workspaces.id, workspaces.orgId],
    }).onDelete('cascade'),
    unique('searches_id_org_key').on(t.id, t.orgId),
    index('searches_workspace_created_idx').on(t.workspaceId, t.createdAt.desc()),
    check('searches_raw_query_length', sql`length(${t.rawQuery}) between 1 and 2000`),
    check('searches_spec_version_positive', sql`${t.specVersion} >= 1`),
  ],
);

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
    foreignKey({
      name: 'research_jobs_search_org_fk',
      columns: [t.searchId, t.orgId],
      foreignColumns: [searches.id, searches.orgId],
    }),
    // Target of composite FKs from usage and task tables.
    unique('research_jobs_id_org_key').on(t.id, t.orgId),
    index('research_jobs_org_status_idx').on(t.orgId, t.status),
    index('research_jobs_workspace_created_idx').on(t.workspaceId, t.createdAt.desc()),
    check(
      'research_jobs_credits_nonnegative',
      sql`${t.creditBudget} >= 0 and ${t.creditsReserved} >= 0 and ${t.creditsUsed} >= 0 and ${t.costMicros} >= 0`,
    ),
  ],
);

/**
 * One unit of planned work in a research job (docs/06 planner output). `parent_task_id` forms a
 * tree within one job; fan-in (a task waiting on several parents) would need an edge table.
 */
export const researchTasks = app.table(
  'research_tasks',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    researchJobId: uuid('research_job_id').notNull(),
    parentTaskId: uuid('parent_task_id'),
    /** `<stage>.<action>`, e.g. `discovery.places_text_search`. */
    type: text('type').notNull(),
    input: jsonb('input').notNull().default({}),
    status: researchTaskStatus('status').notNull().default('queued'),
    attempts: smallint('attempts').notNull().default(0),
    creditBudget: integer('credit_budget').notNull().default(0),
    costMicros: bigint('cost_micros', { mode: 'number' }).notNull().default(0),
    output: jsonb('output'),
    errorClass: errorClass('error_class'),
    startedAt: timestamp('started_at', { withTimezone: true, mode: 'date' }),
    finishedAt: timestamp('finished_at', { withTimezone: true, mode: 'date' }),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    foreignKey({
      name: 'research_tasks_job_org_fk',
      columns: [t.researchJobId, t.orgId],
      foreignColumns: [researchJobs.id, researchJobs.orgId],
    }).onDelete('cascade'),
    // The parent must be in the same job (and therefore the same org, via the job FK above).
    foreignKey({
      name: 'research_tasks_parent_job_fk',
      columns: [t.parentTaskId, t.researchJobId],
      foreignColumns: [t.id, t.researchJobId],
    }).onDelete('cascade'),
    unique('research_tasks_id_org_key').on(t.id, t.orgId),
    unique('research_tasks_id_job_key').on(t.id, t.researchJobId),
    index('research_tasks_parent_idx').on(t.parentTaskId),
    check('research_tasks_not_own_parent', sql`${t.parentTaskId} <> ${t.id}`),
    index('research_tasks_job_status_idx').on(t.researchJobId, t.status),
    check('research_tasks_type_format', sql`${t.type} ~ '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'`),
    check('research_tasks_attempts_range', sql`${t.attempts} between 0 and 5`),
    check('research_tasks_nonnegative', sql`${t.creditBudget} >= 0 and ${t.costMicros} >= 0`),
  ],
);
