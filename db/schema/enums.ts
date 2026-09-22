import { app } from './common.ts';

export const membershipRole = app.enum('membership_role', [
  'owner',
  'admin',
  'manager',
  'member',
  'viewer',
]);

export const sourceType = app.enum('source_type', ['api', 'crawl', 'registry', 'provider', 'user']);

export const tosClass = app.enum('tos_class', ['green', 'amber', 'red']);

export const researchJobStatus = app.enum('research_job_status', [
  'queued',
  'planning',
  'running',
  'paused',
  'completed',
  'failed',
  'cancelled',
]);

export const researchDepth = app.enum('research_depth', ['quick', 'standard', 'deep']);

export const errorClass = app.enum('error_class', [
  'transient',
  'rate_limited',
  'access_restricted',
  'parse_failed',
  'invalid_input',
  'budget_exhausted',
]);

/** docs/11 superset (ADR-0003). */
export const creditReason = app.enum('credit_reason', [
  'grant',
  'purchase',
  'subscription_renewal',
  'reserve',
  'consume',
  'release',
  'refund',
  'expire',
  'adjustment',
]);

export const researchTaskStatus = app.enum('research_task_status', [
  'queued',
  'running',
  'completed',
  'failed',
  'skipped',
  'cancelled',
]);

/** How a stored value was obtained (CLAUDE.md provenance rule). */
export const valueMethod = app.enum('value_method', ['api', 'crawl', 'ai', 'user', 'provider']);

export const entityType = app.enum('entity_type', ['company', 'person', 'location']);

export const geoKind = app.enum('geo_kind', ['country', 'state', 'district', 'city', 'locality']);
