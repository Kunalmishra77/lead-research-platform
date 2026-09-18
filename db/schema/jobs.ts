import { sql } from 'drizzle-orm';
import {
  check,
  foreignKey,
  index,
  integer,
  jsonb,
  text,
  timestamp,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { errorClass } from './enums.ts';
import { organizations, workspaces } from './tenancy.ts';

export const jobRunStatus = app.enum('job_run_status', [
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
]);

/**
 * Generic asynchronous jobs that are not research jobs (e.g. `system.ping`). The API creates the
 * row and publishes the envelope; workers update status, attempts and result.
 */
export const jobRuns = app.table(
  'job_runs',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    workspaceId: uuid('workspace_id').notNull(),
    type: text('type').notNull(),
    status: jobRunStatus('status').notNull().default('queued'),
    attempts: integer('attempts').notNull().default(0),
    input: jsonb('input').notNull().default({}),
    result: jsonb('result'),
    errorClass: errorClass('error_class'),
    error: text('error'),
    createdBy: uuid('created_by'),
    startedAt: timestamp('started_at', { withTimezone: true, mode: 'date' }),
    finishedAt: timestamp('finished_at', { withTimezone: true, mode: 'date' }),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    foreignKey({
      name: 'job_runs_workspace_org_fk',
      columns: [t.workspaceId, t.orgId],
      foreignColumns: [workspaces.id, workspaces.orgId],
    }).onDelete('cascade'),
    index('job_runs_workspace_created_idx').on(t.workspaceId, t.createdAt.desc()),
    check('job_runs_type_format', sql`${t.type} ~ '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'`),
    check('job_runs_attempts_range', sql`${t.attempts} between 0 and 5`),
  ],
);
