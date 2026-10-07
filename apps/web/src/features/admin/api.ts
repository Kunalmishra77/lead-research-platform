import 'server-only';

import { notFound, redirect } from 'next/navigation';

import { ApiError, apiFetch } from '@/lib/api/server';

export interface Page<T> {
  items: T[];
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

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const ADMIN_PAGE_SIZE = 50;

/** Only a well-formed cursor is forwarded; anything else starts from the first page. */
function query(cursor: unknown): string {
  const params = new URLSearchParams({ limit: String(ADMIN_PAGE_SIZE) });
  if (typeof cursor === 'string' && UUID.test(cursor)) params.set('cursor', cursor);
  return params.toString();
}

/**
 * Pages render in parallel with the layout's staff check, so they must not surface API refusals
 * as error pages: 403 (not staff) becomes the same 404 the layout gives, 401 goes to sign-in.
 */
async function adminFetch<T>(path: string): Promise<T> {
  try {
    return await apiFetch<T>(path);
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) notFound();
    if (err instanceof ApiError && err.status === 401) redirect('/login');
    throw err;
  }
}

/** Staff-only, audited on every call (app.admin_list_orgs). */
export function listOrgs(cursor: unknown): Promise<Page<AdminOrg>> {
  return adminFetch<Page<AdminOrg>>(`/admin/orgs?${query(cursor)}`);
}

/** Staff-only, audited on every call (app.admin_list_users). */
export function listUsers(cursor: unknown): Promise<Page<AdminUser>> {
  return adminFetch<Page<AdminUser>>(`/admin/users?${query(cursor)}`);
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
export type ResearchJobStatus = (typeof RESEARCH_JOB_STATUSES)[number];

export interface AdminJob {
  id: string;
  orgId: string;
  orgName: string;
  status: ResearchJobStatus;
  depth: string;
  creditBudget: number;
  creditsReserved: number;
  creditsUsed: number;
  costMicros: number;
  progress: Record<string, unknown>;
  errorClass: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
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

export interface AdminMeter {
  meter: string;
  events: number;
  units: number;
  credits: number;
  costMicros: number;
  orgs: number;
  lastEventAt: string | null;
}

/** A status from the URL is only forwarded when it is one we know; anything else means "all". */
export function asJobStatus(value: unknown): ResearchJobStatus | undefined {
  return RESEARCH_JOB_STATUSES.find((s) => s === value);
}

/** Staff-only, audited on every call (app.admin_list_jobs). */
export function listJobs(cursor: unknown, status?: ResearchJobStatus): Promise<Page<AdminJob>> {
  const params = new URLSearchParams(query(cursor));
  if (status) params.set('status', status);
  return adminFetch<Page<AdminJob>>(`/admin/jobs?${params.toString()}`);
}

/** Staff-only, audited on every call (app.admin_job_tasks). */
export function jobTasks(jobId: string): Promise<AdminTask[]> {
  return adminFetch<AdminTask[]>(`/admin/jobs/${encodeURIComponent(jobId)}/tasks`);
}

/** Staff-only, audited on every call (app.admin_connector_health). */
export function connectorHealth(): Promise<AdminConnector[]> {
  return adminFetch<AdminConnector[]>('/admin/connectors');
}

/** Staff-only, audited on every call (app.admin_usage_by_meter). */
export function usageByMeter(): Promise<AdminMeter[]> {
  return adminFetch<AdminMeter[]>('/admin/usage');
}
