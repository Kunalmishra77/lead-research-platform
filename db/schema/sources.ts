import { sql } from 'drizzle-orm';
import { bigint, boolean, check, integer, jsonb, text, unique, uuid } from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { sourceType, tosClass } from './enums.ts';

/** Global (no RLS): registry of data sources and their policy (docs/08). Seeded, admin-managed. */
export const sources = app.table(
  'sources',
  {
    id: uuid('id').primaryKey(),
    key: text('key').notNull(),
    name: text('name').notNull(),
    type: sourceType('type').notNull(),
    tosClass: tosClass('tos_class').notNull(),
    legalApproved: boolean('legal_approved').notNull().default(false),
    reliability: jsonb('reliability').notNull().default({}),
    costPerCallMicros: bigint('cost_per_call_micros', { mode: 'number' }).notNull().default(0),
    defaultTtlDays: integer('default_ttl_days').notNull().default(30),
    enabled: boolean('enabled').notNull().default(false),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    unique('sources_key_key').on(t.key),
    check('sources_key_format', sql`${t.key} ~ '^[a-z][a-z0-9_]{1,63}$'`),
    // Red sources can only be enabled after legal review (docs/08 source policy).
    check(
      'sources_red_needs_legal',
      sql`not (${t.tosClass} = 'red' and ${t.enabled} and not ${t.legalApproved})`,
    ),
  ],
);
