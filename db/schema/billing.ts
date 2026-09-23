import { sql } from 'drizzle-orm';
import {
  bigint,
  check,
  foreignKey,
  index,
  integer,
  primaryKey,
  text,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { creditReason } from './enums.ts';
import { researchJobs } from './research.ts';
import { organizations } from './tenancy.ts';

/** Append-only (UPDATE/DELETE revoked in the custom migration). docs/11 ledger model. */
export const creditLedger = app.table(
  'credit_ledger',
  {
    id: uuid('id').primaryKey(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    delta: bigint('delta', { mode: 'number' }).notNull(),
    balanceAfter: bigint('balance_after', { mode: 'number' }).notNull(),
    reason: creditReason('reason').notNull(),
    refType: text('ref_type'),
    refId: uuid('ref_id'),
    createdBy: uuid('created_by'),
    createdAt: createdAt(),
  },
  (t) => [
    index('credit_ledger_org_created_idx').on(t.orgId, t.createdAt.desc()),
    check('credit_ledger_delta_nonzero', sql`${t.delta} <> 0`),
  ],
);

/**
 * Idempotency keys for usage_events (ADR-0003): usage_events is partitioned, so this small table
 * holds the cross-partition uniqueness. Insert it in the same transaction as the usage event.
 */
export const usageUnitKeys = app.table(
  'usage_unit_keys',
  {
    orgId: uuid('org_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    researchJobId: uuid('research_job_id').notNull(),
    meter: text('meter').notNull(),
    unitKey: text('unit_key').notNull(),
    usageEventId: uuid('usage_event_id').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    // org_id in the key + composite FK: one org can never occupy another org's idempotency keys.
    primaryKey({
      name: 'usage_unit_keys_pkey',
      columns: [t.orgId, t.researchJobId, t.meter, t.unitKey],
    }),
    foreignKey({
      name: 'usage_unit_keys_job_org_fk',
      columns: [t.researchJobId, t.orgId],
      foreignColumns: [researchJobs.id, researchJobs.orgId],
    }).onDelete('cascade'),
  ],
);

/** Credit price per metered unit (docs/11). Seeded data, read-only for both app roles. */
export const creditRates = app.table(
  'credit_rates',
  {
    meter: text('meter').primaryKey(),
    creditsPerUnit: integer('credits_per_unit').notNull(),
    unit: text('unit').notNull(),
    description: text('description').notNull(),
    updatedAt: updatedAt(),
  },
  (t) => [
    check('credit_rates_meter_format', sql`${t.meter} ~ '^[a-z][a-z0-9_]{1,63}$'`),
    check('credit_rates_nonnegative', sql`${t.creditsPerUnit} >= 0`),
  ],
);

/** Plan allowances (docs/11). Phase 2 reads `signup_credits`; billing arrives in Phase 7. */
export const plans = app.table(
  'plans',
  {
    plan: text('plan').primaryKey(),
    name: text('name').notNull(),
    signupCredits: integer('signup_credits').notNull().default(0),
    monthlyCredits: integer('monthly_credits').notNull().default(0),
    seats: integer('seats').notNull().default(1),
    updatedAt: updatedAt(),
  },
  (t) => [
    check(
      'plans_nonnegative',
      sql`${t.signupCredits} >= 0 and ${t.monthlyCredits} >= 0 and ${t.seats} >= 1`,
    ),
  ],
);
