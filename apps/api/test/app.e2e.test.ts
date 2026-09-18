import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';

describe('API skeleton (healthy dependencies)', () => {
  let app: NestFastifyApplication;

  beforeAll(async () => {
    app = await createTestApp({ dbOk: true, redisOk: true, storageOk: true });
  });

  afterAll(async () => {
    await app.close();
  });

  it('GET /health/live is always ok', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/live' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: 'ok' });
  });

  it('GET /health/ready reports every dependency', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/ready' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: 'ok', checks: { db: 'ok', redis: 'ok', storage: 'ok' } });
  });

  it('generates a UUID v7 request id and returns it', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/live' });
    expect(res.headers['x-request-id']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-/);
  });

  it('reuses a well-formed incoming request id and ignores a malformed one', async () => {
    const good = await app.inject({
      method: 'GET',
      url: '/health/live',
      headers: { 'x-request-id': 'trace-abc-12345' },
    });
    expect(good.headers['x-request-id']).toBe('trace-abc-12345');
    const bad = await app.inject({
      method: 'GET',
      url: '/health/live',
      headers: { 'x-request-id': 'bad id <script>' },
    });
    expect(bad.headers['x-request-id']).not.toBe('bad id <script>');
  });

  it('unknown routes return problem+json 404 with the request id', async () => {
    const res = await app.inject({ method: 'GET', url: '/nope' });
    expect(res.statusCode).toBe(404);
    expect(res.headers['content-type']).toContain('application/problem+json');
    const body = res.json<Record<string, unknown>>();
    expect(body).toMatchObject({
      status: 404,
      code: 'not_found',
      type: 'https://leadforge.dev/problems/not_found',
    });
    expect(body.request_id).toBe(res.headers['x-request-id']);
  });

  it('Zod validation errors return 400 invalid_input with field paths', async () => {
    const res = await app.inject({
      method: 'POST',
      url: '/__probe/echo',
      payload: { name: 'x', count: -1 },
    });
    expect(res.statusCode).toBe(400);
    const body = res.json<{ code: string; errors: { path: string }[] }>();
    expect(body.code).toBe('invalid_input');
    expect(body.errors.map((e) => e.path).sort()).toEqual(['count', 'name']);
  });

  it('valid bodies pass through the global Zod pipe', async () => {
    const res = await app.inject({
      method: 'POST',
      url: '/__probe/echo',
      payload: { name: 'ok', count: 2 },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ name: 'ok', count: 2 });
  });

  it('AppError maps to its code, status and detail', async () => {
    const res = await app.inject({ method: 'GET', url: '/__probe/app-error' });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({
      code: 'research.not_found',
      detail: 'No research job with that id in this workspace',
    });
  });

  it('unexpected errors return a generic 500 without internal details', async () => {
    const res = await app.inject({ method: 'GET', url: '/__probe/crash' });
    expect(res.statusCode).toBe(500);
    expect(res.json()).toMatchObject({ code: 'internal_error', title: 'Internal server error' });
    expect(res.body).not.toContain('secret internal detail');
  });

  it('bodies over the 1 MB limit are rejected with 413', async () => {
    const res = await app.inject({
      method: 'POST',
      url: '/__probe/echo',
      payload: JSON.stringify({ name: 'x'.repeat(1_100_000), count: 1 }),
      headers: { 'content-type': 'application/json' },
    });
    expect(res.statusCode).toBe(413);
    expect(res.headers['content-type']).toContain('application/problem+json');
  });

  it('serves the OpenAPI document', async () => {
    const res = await app.inject({ method: 'GET', url: '/docs/json' });
    expect(res.statusCode).toBe(200);
    const doc = res.json<{ openapi: string; paths: Record<string, unknown> }>();
    expect(doc.openapi).toMatch(/^3\./);
    expect(Object.keys(doc.paths)).toEqual(
      expect.arrayContaining(['/health/live', '/health/ready']),
    );
  });
});

describe('API skeleton (Redis down)', () => {
  let app: NestFastifyApplication;

  beforeAll(async () => {
    app = await createTestApp({ dbOk: true, redisOk: false, storageOk: true });
  });

  afterAll(async () => {
    await app.close();
  });

  it('GET /health/ready returns 503 and names the failing dependency', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/ready' });
    expect(res.statusCode).toBe(503);
    expect(res.json()).toEqual({
      status: 'fail',
      checks: { db: 'ok', redis: 'fail', storage: 'ok' },
    });
  });

  it('liveness does not depend on Redis', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/live' });
    expect(res.statusCode).toBe(200);
  });
});

describe('API skeleton (storage hangs)', () => {
  let app: NestFastifyApplication;
  let aborted = 0;

  beforeAll(async () => {
    app = await createTestApp({
      dbOk: true,
      redisOk: true,
      storageOk: true,
      storageHangs: true,
      onStorageAbort: () => {
        aborted += 1;
      },
    });
  });

  afterAll(async () => {
    await app.close();
  });

  it('times out the probe, aborts the request and reports 503', async () => {
    const started = Date.now();
    const res = await app.inject({ method: 'GET', url: '/health/ready' });
    expect(res.statusCode).toBe(503);
    expect(res.json()).toMatchObject({ checks: { storage: 'fail', db: 'ok', redis: 'ok' } });
    expect(Date.now() - started).toBeLessThan(5000);
    expect(aborted).toBe(1);
  });
});
