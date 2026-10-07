import { describe, expect, it } from 'vitest';

import { AppError } from '../../common/errors/app-error';
import {
  mapAdminError,
  toAdminConnector,
  toAdminJob,
  toAdminMeter,
  toAdminOrg,
  toAdminTask,
  toAdminUser,
  toPage,
} from './admin.mapping';

const org = (id: string) => ({
  id,
  name: 'Acme',
  slug: 'acme',
  plan: 'free',
  region: 'in',
  created_at: new Date('2026-09-18T09:00:00Z'),
  member_count: '3',
});

describe('toPage', () => {
  it('returns a cursor only when the extra row shows another page exists', () => {
    const full = toPage(2, [org('a'), org('b'), org('c')], toAdminOrg);
    expect(full.items.map((o) => o.id)).toEqual(['a', 'b']);
    expect(full.nextCursor).toBe('b');

    const exact = toPage(2, [org('a'), org('b')], toAdminOrg);
    expect(exact.nextCursor).toBeNull();

    expect(toPage(2, [], toAdminOrg)).toEqual({ items: [], nextCursor: null });
  });
});

describe('row mapping', () => {
  it('normalises driver types', () => {
    expect(toAdminOrg(org('a'))).toMatchObject({
      createdAt: '2026-09-18T09:00:00.000Z',
      memberCount: 3,
    });
    expect(
      toAdminUser({
        id: 'u',
        email: null,
        created_at: null,
        email_confirmed_at: '2026-09-18T09:00:00+00:00',
        last_sign_in_at: null,
        is_platform_staff: false,
        org_count: 0,
      }),
    ).toEqual({
      id: 'u',
      email: null,
      createdAt: null,
      emailConfirmedAt: '2026-09-18T09:00:00.000Z',
      lastSignInAt: null,
      isPlatformStaff: false,
      orgCount: 0,
    });
  });

  it('rejects unexpected row shapes', () => {
    expect(() => toAdminOrg({ id: 1 })).toThrow();
  });

  it('keeps a job row honest about what it has not got', () => {
    // settled_at null and credits_used 0 on a completed job is the live state of the credit gap
    // recorded against 2.11: the mapping must carry it through rather than tidy it into a zero.
    const job = toAdminJob({
      id: '01a0d764-0000-7000-8000-000000000001',
      org_id: 'o1',
      org_name: 'Agentix',
      status: 'completed',
      depth: 'quick',
      credit_budget: 20,
      credits_reserved: '20',
      credits_used: '0',
      cost_micros: '0',
      progress: { candidates: 12, leads: 7, values: 164 },
      error_class: null,
      created_at: new Date('2026-09-25T07:56:00Z'),
      started_at: '2026-09-25T07:56:05+00:00',
      finished_at: '2026-09-25T08:00:35+00:00',
      settled_at: null,
      tasks_total: '3',
      tasks_failed: '0',
    });
    expect(job).toMatchObject({
      orgName: 'Agentix',
      status: 'completed',
      creditsReserved: 20,
      creditsUsed: 0,
      settledAt: null,
      tasksTotal: 3,
      progress: { candidates: 12, leads: 7, values: 164 },
    });
  });

  it('rejects a job status outside the enum', () => {
    expect(() => toAdminJob({ id: 'j', status: 'exploded' })).toThrow();
  });

  it('maps a task row, including a duration that cannot be computed yet', () => {
    const running = toAdminTask({
      id: 't1',
      parent_task_id: 'p1',
      type: 'discovery.places_text_search',
      status: 'running',
      attempts: '1',
      credit_budget: 3,
      cost_micros: '0',
      error_class: null,
      started_at: '2026-09-25T07:56:10+00:00',
      finished_at: null,
      duration_ms: null,
      input: { query: 'dental clinic in Delhi' },
    });
    expect(running).toMatchObject({
      parentTaskId: 'p1',
      type: 'discovery.places_text_search',
      attempts: 1,
      durationMs: null,
      input: { query: 'dental clinic in Delhi' },
    });
  });

  it('maps connector health with every error class as its own number', () => {
    const row = toAdminConnector({
      source: 'discovery.places_text_search',
      total: '45',
      completed: '43',
      failed: '2',
      running: '0',
      queued: '0',
      transient: '2',
      rate_limited: '0',
      access_restricted: '0',
      parse_failed: '0',
      invalid_input: '0',
      budget_exhausted: '0',
      avg_ms: '74251',
      last_run_at: '2026-09-25T08:00:35.673+00:00',
    });
    expect(row).toMatchObject({
      total: 45,
      completed: 43,
      failed: 2,
      transient: 2,
      rateLimited: 0,
      accessRestricted: 0,
      avgMs: 74251,
    });
    // There is no cost here on purpose: a task's cost_micros is written as 0 (migration 0022).
    expect(row).not.toHaveProperty('costMicros');
  });

  it('maps usage by meter, which is where cost really is', () => {
    expect(
      toAdminMeter({
        meter: 'api_google_places',
        events: '42',
        units: '42',
        credits: '0',
        cost_micros: '1470000',
        orgs: '1',
        last_event_at: new Date('2026-09-25T08:00:35Z'),
      }),
    ).toEqual({
      meter: 'api_google_places',
      events: 42,
      units: 42,
      credits: 0,
      costMicros: 1470000,
      orgs: 1,
      lastEventAt: '2026-09-25T08:00:35.000Z',
    });
  });
});

describe('mapAdminError', () => {
  it('maps insufficient_privilege anywhere in the cause chain to admin.forbidden', () => {
    const driver = Object.assign(new Error('platform staff only'), { code: '42501' });
    const mapped = mapAdminError(new Error('query failed', { cause: driver }));
    expect(mapped).toBeInstanceOf(AppError);
    expect(mapped).toMatchObject({
      code: 'admin.forbidden',
      httpStatus: 403,
      errorClass: 'access_restricted',
    });
  });

  it('passes other errors through unchanged', () => {
    const other = Object.assign(new Error('boom'), { code: '08006' });
    expect(mapAdminError(other)).toBe(other);
    const app = new AppError({ code: 'x', httpStatus: 401, title: 'x' });
    expect(mapAdminError(app)).toBe(app);
  });
});
