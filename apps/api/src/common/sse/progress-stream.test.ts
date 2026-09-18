import { EventEmitter } from 'node:events';

import type { FastifyReply, FastifyRequest } from 'fastify';
import type { Redis } from 'ioredis';
import type { PinoLogger } from 'nestjs-pino';
import { describe, expect, it, vi } from 'vitest';

import { MAX_STREAMS_PER_USER, ProgressHub } from './progress-hub';
import { streamProgress } from './progress-stream';

class FakeRes extends EventEmitter {
  chunks: string[] = [];
  destroyed = false;
  writableEnded = false;
  headers: Record<string, string> = {};
  writeHead(_status: number, headers: Record<string, string>) {
    this.headers = headers;
  }
  write(chunk: string) {
    if (this.writableEnded) throw new Error('write after end');
    this.chunks.push(chunk);
    return true;
  }
  end() {
    this.writableEnded = true;
  }
  events() {
    return this.chunks.filter((c) => c.startsWith('event:')).map((c) => c.split('\n')[0]?.slice(7));
  }
}

function fakeHub() {
  let listener: ((m: string) => void) | undefined;
  const release = vi.fn();
  const unlisten = vi.fn(() => Promise.resolve());
  const hub = {
    acquire: vi.fn(() => release),
    listen: vi.fn((_channel: string, l: (m: string) => void) => {
      listener = l;
      return Promise.resolve(unlisten);
    }),
  };
  return {
    hub: hub as unknown as ProgressHub,
    emit: (m: unknown) => listener?.(JSON.stringify(m)),
    release,
    unlisten,
    acquire: hub.acquire,
  };
}

function setup() {
  const res = new FakeRes();
  const request = { id: 'req-1', raw: new EventEmitter() } as unknown as FastifyRequest;
  const hijack = vi.fn();
  const reply = { hijack, raw: res } as unknown as FastifyReply;
  const logger = { error: vi.fn(), warn: vi.fn() } as unknown as PinoLogger;
  return { res, request, reply, logger, hijack };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('streamProgress', () => {
  it('keeps state -> progress -> done order even when the terminal event arrives first', async () => {
    const { res, request, reply, logger } = setup();
    const { hub, emit } = fakeHub();
    let status = 'running';
    let resolveState: ((s: { status: string }) => void) | undefined;
    const loadState = vi.fn(
      () =>
        new Promise<{ status: string }>((resolve) => {
          resolveState = resolve;
        }),
    );
    const done = streamProgress(request, reply, hub, logger, {
      channel: 'progress:x',
      userId: 'u',
      loadState,
      isTerminal: (s) => s.status === 'completed',
    });
    await flush();
    emit({ status: 'completed' }); // arrives before the initial state was read
    resolveState?.({ status });
    status = 'completed';
    loadState.mockImplementation(() => Promise.resolve({ status }));
    await done;
    await flush();
    expect(res.events()).toEqual(['state', 'progress', 'done']);
    expect(res.writableEnded).toBe(true);
    expect(res.headers['x-request-id']).toBe('req-1');
  });

  it('ends immediately with done when the job is already finished', async () => {
    const { res, request, reply, logger } = setup();
    const { hub, release, unlisten } = fakeHub();
    await streamProgress(request, reply, hub, logger, {
      channel: 'progress:x',
      userId: 'u',
      loadState: () => Promise.resolve({ status: 'failed' }),
      isTerminal: () => true,
    });
    expect(res.events()).toEqual(['state', 'done']);
    expect(release).toHaveBeenCalledOnce();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it('cleans up when the client disconnects and never writes afterwards', async () => {
    const { res, request, reply, logger } = setup();
    const { hub, emit, release, unlisten } = fakeHub();
    await streamProgress(request, reply, hub, logger, {
      channel: 'progress:x',
      userId: 'u',
      loadState: () => Promise.resolve({ status: 'running' }),
      isTerminal: () => false,
    });
    res.emit('close');
    emit({ status: 'running' });
    expect(release).toHaveBeenCalledOnce();
    expect(unlisten).toHaveBeenCalledOnce();
    expect(res.events()).toEqual(['state']);
  });

  it('closes at the max duration', async () => {
    vi.useFakeTimers();
    try {
      const { res, request, reply, logger } = setup();
      const { hub, release } = fakeHub();
      await streamProgress(request, reply, hub, logger, {
        channel: 'progress:x',
        userId: 'u',
        loadState: () => Promise.resolve({ status: 'running' }),
        isTerminal: () => false,
        heartbeatMs: 60_000,
        maxDurationMs: 5_000,
      });
      vi.advanceTimersByTime(5_000);
      expect(res.writableEnded).toBe(true);
      expect(release).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it('rejects before any header when the user has too many streams', async () => {
    const { request, reply, logger, hijack } = setup();
    const { hub, acquire } = fakeHub();
    acquire.mockImplementation(() => {
      throw Object.assign(new Error('too many'), { code: 'progress.too_many_streams' });
    });
    await expect(
      streamProgress(request, reply, hub, logger, {
        channel: 'c',
        userId: 'u',
        loadState: () => Promise.resolve({}),
        isTerminal: () => false,
      }),
    ).rejects.toMatchObject({ code: 'progress.too_many_streams' });
    expect(hijack).not.toHaveBeenCalled();
  });
});

describe('ProgressHub', () => {
  function hubWithFakeRedis() {
    const subscriber = Object.assign(new EventEmitter(), {
      connect: vi.fn(() => Promise.resolve()),
      subscribe: vi.fn(() => Promise.resolve(1)),
      unsubscribe: vi.fn(() => Promise.resolve(0)),
      disconnect: vi.fn(),
    });
    const duplicate = vi.fn(() => subscriber);
    const redis = { duplicate } as unknown as Redis;
    return { hub: new ProgressHub(redis), subscriber, duplicate };
  }

  it('caps concurrent streams per user and frees slots on release', () => {
    const { hub } = hubWithFakeRedis();
    const releases = Array.from({ length: MAX_STREAMS_PER_USER }, () => hub.acquire('u1'));
    expect(() => hub.acquire('u1')).toThrow();
    expect(() => hub.acquire('u2')).not.toThrow();
    releases[0]?.();
    releases[0]?.(); // idempotent
    expect(() => hub.acquire('u1')).not.toThrow();
  });

  it('multiplexes channels over one connection and unsubscribes after the last listener', async () => {
    const { hub, subscriber, duplicate } = hubWithFakeRedis();
    const a = vi.fn();
    const b = vi.fn();
    const stopA = await hub.listen('progress:1', a);
    const stopB = await hub.listen('progress:1', b);
    expect(duplicate).toHaveBeenCalledOnce();
    expect(subscriber.subscribe).toHaveBeenCalledOnce();
    subscriber.emit('message', 'progress:1', 'hello');
    subscriber.emit('message', 'progress:2', 'other');
    expect(a).toHaveBeenCalledWith('hello');
    expect(b).toHaveBeenCalledWith('hello');
    await stopA();
    expect(subscriber.unsubscribe).not.toHaveBeenCalled();
    await stopB();
    expect(subscriber.unsubscribe).toHaveBeenCalledWith('progress:1');
  });
});
