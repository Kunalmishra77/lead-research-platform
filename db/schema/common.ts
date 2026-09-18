import { pgSchema, timestamp } from 'drizzle-orm/pg-core';

/** Every table we own lives in schema `app` (not exposed via the Supabase Data API, ADR-0002). */
export const app = pgSchema('app');

/** timestamptz, UTC, set by the database. */
export const createdAt = () =>
  timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow();

/** Kept current by the `app.set_updated_at` trigger (custom migration). */
export const updatedAt = () =>
  timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow();
