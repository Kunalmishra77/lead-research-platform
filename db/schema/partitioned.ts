/**
 * Monthly range-partitioned tables (ADR-0003). drizzle-kit cannot create partitioned tables, so
 * this file is NOT in drizzle.config.ts `schema`; the DDL lives in a custom SQL migration and these
 * definitions exist only for typed queries. Keep both in sync.
 */
import {
  bigint,
  boolean,
  customType,
  inet,
  integer,
  jsonb,
  primaryKey,
  real,
  text,
  timestamp,
  uuid,
} from 'drizzle-orm/pg-core';

import { app } from './common.ts';
import { accessStatus, entityType, valueMethod } from './enums.ts';

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

/**
 * `bytea`, which drizzle has no column type for. sha256 digests only here, so 32 bytes.
 */
const bytea = customType<{ data: Buffer; driverData: Buffer }>({
  dataType: () => 'bytea',
});

/**
 * One raw response the crawler kept (task 3.6, docs/04, ADR-0019).
 *
 * The row is written whether or not a body was stored: `url_hash`, `content_hash`, `http_status`,
 * `bytes`, `access_status` and `fetched_at` together say "this URL answered with exactly these
 * bytes at this time", and a content hash proves a page has not changed since without keeping a
 * copy of it. Bodies live on the worker host's own disk, not in object storage -- a lakh of
 * leads is about 660 MB of them against a 1 GB free plan, and ADR-0019 has the measurement.
 */
export const rawDocuments = app.table(
  'raw_documents',
  {
    id: uuid('id').notNull(),
    url: text('url').notNull(),
    /** sha256 of the URL as fetched: a URL can exceed what btree will take. */
    urlHash: bytea('url_hash').notNull(),
    sourceId: uuid('source_id').notNull(),
    /** When the fetch happened, and the partition key. */
    fetchedAt: timestamp('fetched_at', { withTimezone: true, mode: 'date' }).notNull(),
    httpStatus: integer('http_status'),
    contentType: text('content_type'),
    /** sha256 of the body; null when there was no body to hash. */
    contentHash: bytea('content_hash'),
    /** Where the body is, under the key docs/06 §4.9 defines; null when nothing was stored. */
    storageKey: text('storage_key'),
    bytes: integer('bytes'),
    accessStatus: accessStatus('access_status').notNull(),
  },
  (t) => [primaryKey({ name: 'raw_documents_pkey', columns: [t.id, t.fetchedAt] })],
);

/**
 * Cross-partition uniqueness for raw documents, and the only thing
 * `field_values.raw_document_id` can point at (ADR-0003, ADR-0019).
 *
 * `unique(url_hash, content_hash)` cannot be enforced on a table partitioned by `fetched_at`,
 * because every unique constraint there must contain the partition key -- so the same body
 * fetched in two months would be two rows. This is also what makes content-hash dedupe one
 * indexed lookup.
 *
 * NOT partitioned, so it is in this file only to sit beside the table it keys.
 */
export const rawDocumentKeys = app.table('raw_document_keys', {
  rawDocumentId: uuid('raw_document_id').primaryKey(),
  urlHash: bytea('url_hash').notNull(),
  contentHash: bytea('content_hash').notNull(),
  fetchedAt: timestamp('fetched_at', { withTimezone: true, mode: 'date' }).notNull(),
  storageKey: text('storage_key'),
  createdAt: createdAt(),
});
