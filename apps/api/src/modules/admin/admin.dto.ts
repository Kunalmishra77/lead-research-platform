import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

export const AdminListQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  /** `nextCursor` of the previous page (docs/05 pagination convention). */
  cursor: z.uuid().optional(),
});
export class AdminListQueryDto extends createZodDto(AdminListQuerySchema) {}

export interface Page<T> {
  items: T[];
  /** Pass as `cursor` for the next page; null on the last page. */
  nextCursor: string | null;
}

export interface AdminOrg {
  id: string;
  name: string;
  slug: string;
  plan: string;
  region: string;
  createdAt: string;
  memberCount: number;
}

export interface AdminUser {
  id: string;
  email: string | null;
  createdAt: string | null;
  emailConfirmedAt: string | null;
  lastSignInAt: string | null;
  isPlatformStaff: boolean;
  orgCount: number;
}

export const RESEARCH_JOB_STATUSES = [
  'queued',
  'planning',
  'running',
  'paused',
  'completed',
  'failed',
  'cancelled',
] as const;

export const AdminJobsQuerySchema = AdminListQuerySchema.extend({
  /** Narrow to one status; the common use is `failed` or `running`. */
  status: z.enum(RESEARCH_JOB_STATUSES).optional(),
});
export class AdminJobsQueryDto extends createZodDto(AdminJobsQuerySchema) {}

export const AdminSinceQuerySchema = z.object({
  /** Window start. Defaults to seven days ago in the function, not here. */
  since: z.iso.datetime({ offset: true }).optional(),
});
export class AdminSinceQueryDto extends createZodDto(AdminSinceQuerySchema) {}

export interface AdminJob {
  id: string;
  orgId: string;
  orgName: string;
  status: (typeof RESEARCH_JOB_STATUSES)[number];
  depth: string;
  creditBudget: number;
  creditsReserved: number;
  creditsUsed: number;
  costMicros: number;
  /** The counters the worker accumulates: candidates, leads, values. */
  progress: Record<string, unknown>;
  errorClass: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  /** Null on a job whose reservation was never settled — see PROGRESS.md against 2.11. */
  settledAt: string | null;
  tasksTotal: number;
  tasksFailed: number;
}

export interface AdminTask {
  id: string;
  parentTaskId: string | null;
  type: string;
  status: string;
  attempts: number;
  creditBudget: number;
  costMicros: number;
  errorClass: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  durationMs: number | null;
  input: Record<string, unknown>;
}

/**
 * Outcomes per connector, counted from task rows. Cost is deliberately absent: a task's
 * `cost_micros` is written as 0 by the discovery handler, so the only honest cost figure comes
 * from `AdminMeter` (migration 0022).
 */
export interface AdminConnector {
  source: string;
  total: number;
  completed: number;
  failed: number;
  running: number;
  queued: number;
  transient: number;
  rateLimited: number;
  accessRestricted: number;
  parseFailed: number;
  invalidInput: number;
  budgetExhausted: number;
  avgMs: number | null;
  lastRunAt: string | null;
}

/**
 * How the crawler is doing over a window (task 3.16).
 *
 * One object, not a list: the question is "how is it going", and splitting it per source would
 * repeat `app.admin_connector_health`, which already answers that.
 */
export interface AdminCrawlHealth {
  since: string;
  tasks: number;
  completed: number;
  failed: number;
  running: number;
  pagesRead: number;
  /** Robots said no, the host was dead, or the site turned us away. */
  tasksWithNoPage: number;
  tasksThatFoundNothing: number;
  valuesWritten: number;
  fetches: number;
  fetchedOk: number;
  /** A door we were told not to open. An outcome, not a failure (ADR-0019). */
  restricted: number;
  errored: number;
  bodiesStored: number;
  /** A page we already had: recorded, not stored again (docs/06 §4.9). */
  deduplicated: number;
  bytesStored: number;
  avgMs: number | null;
  p95Ms: number | null;
  /** Every metered call in the window. The crawl itself calls no paid API. */
  pipelineCostMicros: number;
  leadsDelivered: number;
  /** Null when nothing was delivered: a zero would read as free. */
  pipelineMicrosPerLead: number | null;
  lastCrawlAt: string | null;
}

export interface AdminMeter {
  meter: string;
  events: number;
  units: number;
  credits: number;
  costMicros: number;
  orgs: number;
  lastEventAt: string | null;
}
