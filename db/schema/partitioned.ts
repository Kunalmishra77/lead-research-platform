/**
 * Monthly range-partitioned tables (ADR-0003). drizzle-kit cannot create partitioned tables, so
 * this file is NOT in drizzle.config.ts `schema`; the DDL lives in a custom SQL migration and these
 * definitions exist only for typed queries. Keep both in sync.
 */
import { bigint, inet, jsonb, primaryKey, text, timestamp, uuid } from 'drizzle-orm/pg-core';

import { app } from './common.ts';

const createdAt = () =>
  timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow();

/** Append-only. org_id is null only for platform-level events written by definer functions. */
export const auditLogs = app.table(
  'audit_logs',
  {
    id: uuid('id').notNull(),
    orgId: uuid('org_id'),
    actorUserId: uuid('actor_user_id'),
    actorApiKeyId: uuid('actor_api_key_id'),
    action: text('action').notNull(),
    targetType: text('target_type'),
    targetId: text('target_id'),
    ip: inet('ip'),
    userAgent: text('user_agent'),
    meta: jsonb('meta').notNull().default({}),
    createdAt: createdAt(),
  },
  (t) => [primaryKey({ name: 'audit_logs_pkey', columns: [t.id, t.createdAt] })],
);

export const usageEvents = app.table(
  'usage_events',
  {
    id: uuid('id').notNull(),
    orgId: uuid('org_id').notNull(),
    userId: uuid('user_id'),
    researchJobId: uuid('research_job_id'),
    meter: text('meter').notNull(),
    units: bigint('units', { mode: 'number' }).notNull(),
    credits: bigint('credits', { mode: 'number' }).notNull().default(0),
    costMicros: bigint('cost_micros', { mode: 'number' }).notNull().default(0),
    unitKey: text('unit_key').notNull(),
    createdAt: createdAt(),
  },
  (t) => [primaryKey({ name: 'usage_events_pkey', columns: [t.id, t.createdAt] })],
);
