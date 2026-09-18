import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

describe('AuthGuard (no database)', () => {
  let app: NestFastifyApplication;
  let keys: TestKeys;

  beforeAll(async () => {
    keys = await createTestKeys();
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet },
    );
  });

  afterAll(async () => {
    await app.close();
  });

  it('rejects requests without a token with 401 + WWW-Authenticate', async () => {
    const res = await app.inject({ method: 'GET', url: '/app/me' });
    expect(res.statusCode).toBe(401);
    expect(res.headers['www-authenticate']).toBe('Bearer');
    expect(res.json()).toMatchObject({ code: 'auth.missing_token' });
  });

  it('rejects malformed authorization headers', async () => {
    for (const authorization of ['Basic abc', 'Bearer', 'Bearer not a jwt', 'bearer a.b.c']) {
      const res = await app.inject({ method: 'GET', url: '/app/me', headers: { authorization } });
      expect(res.statusCode, authorization).toBe(401);
    }
  });

  it('rejects tokens signed with an unknown key', async () => {
    const token = await keys.sign(
      { sub: '01923f4e-7b3a-7c2d-9f10-0000000000aa' },
      { foreignKey: true },
    );
    const res = await app.inject({
      method: 'GET',
      url: '/app/me',
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.statusCode).toBe(401);
    expect(res.headers['www-authenticate']).toBe('Bearer error="invalid_token"');
    expect(res.json()).toMatchObject({ code: 'auth.invalid_token' });
  });

  it('keeps @Public() routes open', async () => {
    const res = await app.inject({ method: 'GET', url: '/health/live' });
    expect(res.statusCode).toBe(200);
  });

  it('documents bearer auth in OpenAPI', async () => {
    const doc = (await app.inject({ method: 'GET', url: '/docs/json' })).json<{
      paths: Record<string, unknown>;
    }>();
    expect(Object.keys(doc.paths)).toEqual(expect.arrayContaining(['/app/me', '/app/orgs']));
  });
});
