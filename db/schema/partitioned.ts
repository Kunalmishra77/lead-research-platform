/**
 * Monthly range-partitioned tables (ADR-0003). drizzle-kit cannot create partitioned tables, so
 * this file is NOT in drizzle.config.ts `schema`; the DDL lives in a custom SQL migration and these
 * definitions exist only for typed queries. Keep both in sync.
 */
import {
  bigint,
  boolean,
  inet,
  jsonb,
  primaryKey,
  real,
  text,
  timestamp,
  uuid,
} from 'drizzle-orm/pg-core';

import { app } from './common.ts';
import { entityType, valueMethod } from './enums.ts';

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

/**
 * One observed value for one field of one global entity, with provenance (CLAUDE.md: no value
 * without source_id, source_url, observed_at, method and confidence). Append-only except for the
 * `is_current` flag, which the pipeline flips when a newer observation supersedes a row.
 */
export const fieldValues = app.table(
  'field_values',
  {
    id: uuid('id').notNull(),
    entityType: entityType('entity_type').notNull(),
    entityId: uuid('entity_id').notNull(),
    field: text('field').notNull(),
    value: jsonb('value').notNull(),
    sourceId: uuid('source_id').notNull(),
    sourceUrl: text('source_url').notNull(),
    rawDocumentId: uuid('raw_document_id'),
    method: valueMethod('method').notNull(),
    derivation: text('derivation'),
    confidence: real('confidence').notNull(),
    /** When the source showed the value (not ingestion time): always set explicitly. */
    observedAt: timestamp('observed_at', { withTimezone: true, mode: 'date' }).notNull(),
    isCurrent: boolean('is_current').notNull().default(true),
    model: text('model'),
    promptVersion: text('prompt_version'),
  },
  (t) => [primaryKey({ name: 'field_values_pkey', columns: [t.id, t.observedAt] })],
);
