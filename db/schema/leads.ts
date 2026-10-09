import { sql } from 'drizzle-orm';
import {
  boolean,
  check,
  foreignKey,
  index,
  jsonb,
  primaryKey,
  smallint,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { leadStatus } from './enums.ts';
import { companies } from './graph.ts';
import { researchJobs } from './research.ts';
import { organizations, workspaces } from './tenancy.ts';

/**
 * A company a workspace has been given (docs/04, ADR-0012).
 *
 * This is the tenant side of the global graph. `companies` is shared and holds only what public
 * sources said; this table says which workspace was handed that company, by which research job.
 * Without it a job's results cannot be read back at all, and a second workspace running the same
 * search receives nothing — the graph already had every business, so nothing looked new.
 *
 * It is also what "delivered new lead" counts (docs/11): a row here is a lead the customer was
 * given, and the unique key is what stops them being charged twice for the same one.
 *
 * Score, overrides, custom fields and assignment are written by nothing until Phases 4-6. They
 * are here because docs/04 settled their shape and a second migration over a tenant table buys
 * nothing; ADR-0012 records why that departs from ADR-0007's "build only what this phase reads".
 */
export const leads = app.table(
  'leads',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    workspaceId: uuid('workspace_id').notNull(),
    /** The shared company this lead points at. Never deleted by a tenant. */
    companyId: uuid('company_id')
      .notNull()
      .references(() => companies.id, { onDelete: 'cascade' }),
    /** Null until people arrive (Phase 5); one lead per company per workspace until then. */
    personId: uuid('person_id'),
    /** Which job handed this lead over. Null once a lead can be created by import (Phase 4). */
    researchJobId: uuid('research_job_id'),
    /** 0-100, written by scoring (Phase 6). */
    score: smallint('score'),
    scoreBreakdown: jsonb('score_breakdown'),
    status: leadStatus('status').notNull().default('new'),
    assigneeUserId: uuid('assignee_user_id'),
    /**
     * A user's corrections, kept tenant-side. They never reach the graph until reviewed and
     * re-observed as a `method=user` field value (ADR-0007, docs/04).
     */
    overrides: jsonb('overrides').notNull().default({}),
    customFields: jsonb('custom_fields').notNull().default({}),
    contactedAt: timestamp('contacted_at', { withTimezone: true, mode: 'date' }),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    foreignKey({
      name: 'leads_workspace_org_fk',
      columns: [t.workspaceId, t.orgId],
      foreignColumns: [workspaces.id, workspaces.orgId],
    }).onDelete('cascade'),
    foreignKey({
      name: 'leads_job_org_fk',
      columns: [t.researchJobId, t.orgId],
      foreignColumns: [researchJobs.id, researchJobs.orgId],
    }).onDelete('set null'),
    /**
     * One lead per company per workspace. `coalesce` because a plain unique would let the same
     * company be delivered — and charged for — any number of times while `person_id` is null
     * (docs/04).
     */
    uniqueIndex('leads_workspace_company_person_key').on(
      t.workspaceId,
      t.companyId,
      sql`coalesce(${t.personId}, '00000000-0000-0000-0000-000000000000'::uuid)`,
    ),
    index('leads_workspace_score_idx').on(t.workspaceId, t.score.desc()),
    index('leads_workspace_status_idx').on(t.workspaceId, t.status),
    /** The job page reads this one: every lead a given job delivered, newest first. */
    index('leads_job_idx').on(t.researchJobId, t.createdAt.desc()),
    check('leads_score_range', sql`${t.score} is null or ${t.score} between 0 and 100`),
  ],
);

/**
 * Which leads a research job surfaced — new to the workspace or already held (ADR-0015).
 *
 * `leads.research_job_id` says which job *first* delivered a lead, and that is a fact about the
 * lead. This says which jobs *found* it, which is a fact about each job, and the two stop
 * agreeing the moment a customer runs the same search twice: the second run re-finds everything,
 * inserts no lead row, and without this table reports that it found nothing.
 *
 * `is_new` is stored, not derived. "Was this lead new when this job ran?" is answerable only at
 * write time, and a later job must not be able to change what an earlier invoice said. Billing
 * counts `sum(is_new)`; the grid counts rows; both read the same table.
 */
export const researchJobLeads = app.table(
  'research_job_leads',
  {
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    researchJobId: uuid('research_job_id').notNull(),
    leadId: uuid('lead_id')
      .notNull()
      .references(() => leads.id, { onDelete: 'cascade' }),
    /** True if this job is the one that delivered the lead. What docs/11 charges for. */
    isNew: boolean('is_new').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    primaryKey({ name: 'research_job_leads_pkey', columns: [t.researchJobId, t.leadId] }),
    foreignKey({
      name: 'research_job_leads_job_org_fk',
      columns: [t.researchJobId, t.orgId],
      foreignColumns: [researchJobs.id, researchJobs.orgId],
    }).onDelete('cascade'),
    /** The reverse question: every search that turned this company up. */
    index('research_job_leads_lead_idx').on(t.leadId),
  ],
);
