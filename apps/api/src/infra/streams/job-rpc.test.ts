import type { Redis } from 'ioredis';
import { describe, expect, it, vi } from 'vitest';

import { AppError } from '../../common/errors/app-error';
import { JobRpc, REPLY_PREFIX, replyKeyFor } from './job-rpc';

interface FakeRedis {
  blpop: ReturnType<typeof vi.fn>;
  del: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  duplicate: ReturnType<typeof vi.fn>;
  xinfo: ReturnType<typeof vi.fn>;
}

function fakeRedis(overrides: Partial<FakeRedis> = {}): FakeRedis {
  const clone: FakeRedis = {
    blpop: vi.fn().mockResolvedValue(null),
    del: vi.fn().mockResolvedValue(1),
    disconnect: vi.fn(),
    duplicate: vi.fn(),
    xinfo: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
  clone.duplicate.mockReturnValue(clone);
  return clone;
}

const rpc = (redis: FakeRedis) => new JobRpc(redis as unknown as Redis);

describe('JobRpc', () => {
  it('generates a reply key only it could have chosen', () => {
    const client = rpc(fakeRedis());
    const id = client.newJobId();

    expect(replyKeyFor(id).startsWith(REPLY_PREFIX)).toBe(true);
    // UUID v7 like every other id in the API (CLAUDE.md).
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(client.newJobId()).not.toBe(id);
  });

  it('refuses to wait on a key outside the reply prefix', async () => {
    const redis = fakeRedis();

    // A key from anywhere but replyKeyFor would mean someone else chose where a worker writes.
    await expect(rpc(redis).await('jobs:discovery', 1_000)).rejects.toBeInstanceOf(AppError);
    expect(redis.blpop).not.toHaveBeenCalled();
  });

  it('returns the parsed reply and gives its connection back', async () => {
    const key = replyKeyFor('a');
    const redis = fakeRedis({
      blpop: vi.fn().mockResolvedValue([key, JSON.stringify({ ok: true })]),
    });

    await expect(rpc(redis).await(key, 5_000)).resolves.toEqual({ ok: true });
    // One connection per wait, released either way: a shared one would serialise waits.
    expect(redis.duplicate).toHaveBeenCalledTimes(1);
    expect(redis.disconnect).toHaveBeenCalledTimes(1);
  });

  it('cleans up the key when nobody answers, so a late reply reaches no one', async () => {
    const key = replyKeyFor('b');
    const redis = fakeRedis();

    await expect(rpc(redis).await(key, 2_000)).resolves.toBeNull();
    expect(redis.del).toHaveBeenCalledWith(key);
    expect(redis.disconnect).toHaveBeenCalledTimes(1);
  });

  it('reports an unreadable reply as a bad gateway, not a crash', async () => {
    const key = replyKeyFor('c');
    const redis = fakeRedis({ blpop: vi.fn().mockResolvedValue([key, 'not json']) });

    await expect(rpc(redis).await(key, 1_000)).rejects.toMatchObject({
      code: 'jobs.invalid_reply',
      httpStatus: 502,
    });
    expect(redis.disconnect).toHaveBeenCalledTimes(1);
  });

  it('waits at least one whole second, because BLPOP 0 would wait forever', async () => {
    const key = replyKeyFor('d');
    const redis = fakeRedis();

    await rpc(redis).await(key, 10);

    expect(redis.blpop).toHaveBeenCalledWith(key, 1);
  });

  it('gives the connection more time than the wait it is carrying', async () => {
    const key = replyKeyFor('e');
    const redis = fakeRedis();

    await rpc(redis).await(key, 20_000);

    // The shared client sets commandTimeout: 3000 and duplicate() copies it, so without an
    // override ioredis would abort the BLPOP long before it was meant to end.
    const override = redis.duplicate.mock.calls[0]?.[0] as { commandTimeout: number };
    expect(override.commandTimeout).toBeGreaterThan(20_000);
  });

  it('sees a pool with a live consumer, and an empty one', async () => {
    const withConsumer = fakeRedis({
      xinfo: vi.fn().mockResolvedValue([['name', 'workers:interactive', 'consumers', 2]]),
    });
    const idle = fakeRedis({
      xinfo: vi.fn().mockResolvedValue([['name', 'workers:interactive', 'consumers', 0]]),
    });
    const missing = fakeRedis({ xinfo: vi.fn().mockRejectedValue(new Error('no such key')) });

    await expect(
      rpc(withConsumer).hasConsumers('jobs:interactive', 'workers:interactive'),
    ).resolves.toBe(true);
    await expect(rpc(idle).hasConsumers('jobs:interactive', 'workers:interactive')).resolves.toBe(
      false,
    );
    await expect(
      rpc(missing).hasConsumers('jobs:interactive', 'workers:interactive'),
    ).resolves.toBe(false);
  });

  it('does not mistake another pool group for this one', async () => {
    const redis = fakeRedis({
      xinfo: vi.fn().mockResolvedValue([['name', 'workers:discovery', 'consumers', 3]]),
    });

    await expect(rpc(redis).hasConsumers('jobs:interactive', 'workers:interactive')).resolves.toBe(
      false,
    );
  });
});
