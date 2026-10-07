import { z } from 'zod';

import { pgCode } from '../../common/db/pg-error';
import { AppError } from '../../common/errors/app-error';
import {
  type AdminConnector,
  type AdminJob,
  type AdminMeter,
  type AdminOrg,
  type AdminTask,
  type AdminUser,
  type Page,
  RESEARCH_JOB_STATUSES,
} from './admin.dto';

// postgres.js returns timestamptz as Date and int8 as string; validate instead of trusting casts.
const timestamp = z.union([z.date(), z.string()]).transform((v) => new Date(v).toISOString());
const count = z.union([z.string(), z.number(), z.bigint()]).transform(Number);

const OrgRow = z.object({
  id: z.string(),
  name: z.string(),
  slug: z.string(),
  plan: z.string(),
  region: z.string(),
  created_at: timestamp,
  member_count: count,
});

const UserRow = z.object({
  id: z.string(),
  email: z.string().nullable(),
  created_at: timestamp.nullable(), // nullable in auth.users
  email_confirmed_at: timestamp.nullable(),
  last_sign_in_at: timestamp.nullable(),
  is_platform_staff: z.boolean(),
  org_count: count,
});

export function toAdminOrg(raw: unknown): AdminOrg {
  const r = OrgRow.parse(raw);
  return {
    id: r.id,
    name: r.name,
    slug: r.slug,
    plan: r.plan,
    region: r.region,
    createdAt: r.created_at,
    memberCount: r.member_count,
  };
}

export function toAdminUser(raw: unknown): AdminUser {
  const r = UserRow.parse(raw);
  return {
    id: r.id,
    email: r.email,
    createdAt: r.created_at,
    emailConfirmedAt: r.email_confirmed_at,
    lastSignInAt: r.last_sign_in_at,
    isPlatformStaff: r.is_platform_staff,
    orgCount: r.org_count,
  };
}

const jsonObject = z.record(z.string(), z.unknown());

const JobRow = z.object({
  id: z.string(),
  org_id: z.string(),
  org_name: z.string(),
  status: z.enum(RESEARCH_JOB_STATUSES),
  depth: z.string(),
  credit_budget: count,
  credits_reserved: count,
  credits_used: count,
  cost_micros: count,
  progress: jsonObject,
  error_class: z.string().nullable(),
  created_at: timestamp,
  started_at: timestamp.nullable(),
  finished_at: timestamp.nullable(),
  settled_at: timestamp.nullable(),
  tasks_total: count,
  tasks_failed: count,
});

const TaskRow = z.object({
  id: z.string(),
  parent_task_id: z.string().nullable(),
  type: z.string(),
  status: z.string(),
  attempts: count,
  credit_budget: count,
  cost_micros: count,
  error_class: z.string().nullable(),
  started_at: timestamp.nullable(),
  finished_at: timestamp.nullable(),
  duration_ms: count.nullable(),
  input: jsonObject,
});

const ConnectorRow = z.object({
  source: z.string(),
  total: count,
  completed: count,
  failed: count,
  running: count,
  queued: count,
  transient: count,
  rate_limited: count,
  access_restricted: count,
  parse_failed: count,
  invalid_input: count,
  budget_exhausted: count,
  avg_ms: count.nullable(),
  last_run_at: timestamp.nullable(),
});

const MeterRow = z.object({
  meter: z.string(),
  events: count,
  units: count,
  credits: count,
  cost_micros: count,
  orgs: count,
  last_event_at: timestamp.nullable(),
});

export function toAdminJob(raw: unknown): AdminJob {
  const r = JobRow.parse(raw);
  return {
    id: r.id,
    orgId: r.org_id,
    orgName: r.org_name,
    status: r.status,
    depth: r.depth,
    creditBudget: r.credit_budget,
    creditsReserved: r.credits_reserved,
    creditsUsed: r.credits_used,
    costMicros: r.cost_micros,
    progress: r.progress,
    errorClass: r.error_class,
    createdAt: r.created_at,
    startedAt: r.started_at,
    finishedAt: r.finished_at,
    settledAt: r.settled_at,
    tasksTotal: r.tasks_total,
    tasksFailed: r.tasks_failed,
  };
}

export function toAdminTask(raw: unknown): AdminTask {
  const r = TaskRow.parse(raw);
  return {
    id: r.id,
    parentTaskId: r.parent_task_id,
    type: r.type,
    status: r.status,
    attempts: r.attempts,
    creditBudget: r.credit_budget,
    costMicros: r.cost_micros,
    errorClass: r.error_class,
    startedAt: r.started_at,
    finishedAt: r.finished_at,
    durationMs: r.duration_ms,
    input: r.input,
  };
}

export function toAdminConnector(raw: unknown): AdminConnector {
  const r = ConnectorRow.parse(raw);
  return {
    source: r.source,
    total: r.total,
    completed: r.completed,
    failed: r.failed,
    running: r.running,
    queued: r.queued,
    transient: r.transient,
    rateLimited: r.rate_limited,
    accessRestricted: r.access_restricted,
    parseFailed: r.parse_failed,
    invalidInput: r.invalid_input,
    budgetExhausted: r.budget_exhausted,
    avgMs: r.avg_ms,
    lastRunAt: r.last_run_at,
  };
}

export function toAdminMeter(raw: unknown): AdminMeter {
  const r = MeterRow.parse(raw);
  return {
    meter: r.meter,
    events: r.events,
    units: r.units,
    credits: r.credits,
    costMicros: r.cost_micros,
    orgs: r.orgs,
    lastEventAt: r.last_event_at,
  };
}

/**
 * Rows were fetched with `limit + 1`: the extra row only signals that another page exists, so an
 * exact multiple of the page size does not produce an empty last page.
 */
export function toPage<T extends { id: string }>(
  limit: number,
  rows: readonly unknown[],
  map: (raw: unknown) => T,
): Page<T> {
  const items = rows.slice(0, limit).map(map);
  const last = items.at(-1);
  return { items, nextCursor: rows.length > limit && last ? last.id : null };
}

/** insufficient_privilege from the admin functions means "not platform staff". */
export function mapAdminError(err: unknown): unknown {
  if (err instanceof AppError) return err;
  if (pgCode(err) === '42501') {
    return new AppError({
      code: 'admin.forbidden',
      httpStatus: 403,
      title: 'Platform staff only',
      errorClass: 'access_restricted',
      cause: err,
    });
  }
  return err;
}
