import type { JobEnvelope } from '@leadforge/contracts';
import type { Redis } from 'ioredis';
import { describe, expect, it, vi } from 'vitest';

import { JobPublisher } from './job-publisher';

const envelope = (): JobEnvelope => ({
  envelope_version: 1,
  job_id: '01923f4e-7b3a-7c2d-9f10-3a4b5c6d7e8f',
  type: 'system.ping',
  org_id: '01923f4e-7b3a-7c2d-9f10-000000000001',
  research_job_id: null,
  idempotency_key: 'system.ping:x',
  attempt: 1,
  priority: 'interactive',
  budget: { credits_remaining: 0, cost_cap_micros: 0 },
  trace_id: 'abc123',
  payload: {},
  created_at: new Date().toISOString(),
});

function fakeRedis(xadd = vi.fn(() => Promise.resolve('1-0'))) {
  return { redis: { status: 'ready', xadd } as unknown as Redis, xadd };
}

describe('JobPublisher', () => {
  it('publishes valid envelopes to jobs:<pool> with a capped stream', async () => {
    const { redis, xadd } = fakeRedis();
    await expect(new JobPublisher(redis).publish('system', envelope())).resolves.toBe('1-0');
    expect(xadd).toHaveBeenCalledWith(
      'jobs:system',
      'MAXLEN',
      '~',
      '100000',
      '*',
      'envelope',
      expect.stringContaining('"type":"system.ping"'),
    );
  });

  it('never publishes an envelope the workers would reject', async () => {
    const { redis, xadd } = fakeRedis();
    const bad = {
      ...envelope(),
      attempt: 9,
      research_job_id: '01923f4e-7b3a-7c2d-9f10-00000000abcd',
      org_id: null,
    };
    await expect(new JobPublisher(redis).publish('system', bad)).rejects.toMatchObject({
      code: 'jobs.invalid_envelope',
    });
    expect(xadd).not.toHaveBeenCalled();
  });

  it('maps Redis failures to a transient 503', async () => {
    const { redis } = fakeRedis(vi.fn(() => Promise.reject(new Error('ECONNREFUSED'))));
    await expect(new JobPublisher(redis).publish('system', envelope())).rejects.toMatchObject({
      code: 'jobs.queue_unavailable',
      httpStatus: 503,
      errorClass: 'transient',
    });
  });
});
