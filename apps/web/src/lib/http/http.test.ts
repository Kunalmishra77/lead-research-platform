// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { buildApiTarget, isSameOrigin, pickResponseHeaders, readCappedBody } from './api-proxy';
import { safeNextPath } from './safe-next';
import { workspaceCookieOptions } from './workspace-cookie';

const APP = 'https://app.example.com';
const API = 'http://api:4000';

describe('safeNextPath', () => {
  it.each([
    ['/lists?id=1#top', '/lists?id=1#top'],
    ['/dashboard', '/dashboard'],
    ['/a/../b', '/b'],
  ])('keeps same-origin path %s', (input, expected) => {
    expect(safeNextPath(input, APP)).toBe(expected);
  });

  it.each([
    '//evil.com',
    '/\\evil.com',
    '/\t/evil.com',
    '/\n/evil.com',
    'https://evil.com',
    'evil.com',
    'javascript:alert(1)',
    '',
    null,
    undefined,
  ])('rejects %j', (input) => {
    expect(safeNextPath(input, APP)).toBe('/dashboard');
  });
});

describe('buildApiTarget', () => {
  it('maps segments under /app/ and keeps the query', () => {
    expect(buildApiTarget(['members', 'x'], '?a=1', API)?.href).toBe(
      'http://api:4000/app/members/x?a=1',
    );
  });

  it.each([[['..', 'admin', 'x']], [['.']], [['a', '..']], [['a%2F..']], [['a/b']], [[]]])(
    'rejects escaping or malformed path %j',
    (path) => {
      expect(buildApiTarget(path, '', API)).toBeNull();
    },
  );
});

describe('isSameOrigin', () => {
  it('accepts only the exact app origin', () => {
    expect(isSameOrigin(APP, APP)).toBe(true);
    expect(isSameOrigin('https://evil.com', APP)).toBe(false);
    expect(isSameOrigin('https://app.example.com.evil.com', APP)).toBe(false);
    expect(isSameOrigin('null', APP)).toBe(false);
    expect(isSameOrigin(null, APP)).toBe(false);
  });
});

describe('pickResponseHeaders', () => {
  it('forwards the allowlist only and never set-cookie', () => {
    const out = pickResponseHeaders(
      new Headers({
        'content-type': 'application/json',
        'retry-after': '5',
        'set-cookie': 'a=b',
        connection: 'keep-alive',
        server: 'nest',
      }),
    );
    expect([...out.keys()].sort()).toEqual(['content-type', 'retry-after']);
  });

  it('marks SSE as unbuffered and untransformed', () => {
    const out = pickResponseHeaders(new Headers({ 'content-type': 'text/event-stream' }));
    expect(out.get('cache-control')).toBe('no-cache, no-transform');
    expect(out.get('x-accel-buffering')).toBe('no');
  });
});

describe('readCappedBody', () => {
  const stream = (...chunks: number[]) =>
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const n of chunks) controller.enqueue(new Uint8Array(n));
        controller.close();
      },
    });

  it('returns the body within the cap', async () => {
    expect((await readCappedBody(stream(3, 4), 10))?.byteLength).toBe(7);
  });

  it('fails as soon as the cap is exceeded', async () => {
    await expect(readCappedBody(stream(6, 6), 10)).rejects.toThrow();
  });
});

describe('workspaceCookieOptions', () => {
  it('is secure exactly when APP_URL is https', () => {
    expect(workspaceCookieOptions(APP).secure).toBe(true);
    expect(workspaceCookieOptions('http://localhost:3000').secure).toBe(false);
    expect(workspaceCookieOptions(APP)).toMatchObject({ httpOnly: true, sameSite: 'lax' });
  });
});
