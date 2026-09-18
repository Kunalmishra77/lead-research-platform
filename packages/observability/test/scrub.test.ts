import type { ErrorEvent } from '@sentry/core';
import { describe, expect, it } from 'vitest';

import {
  baseSentryOptions,
  redactText,
  scrubBreadcrumb,
  scrubEvent,
  stripQuery,
} from '../src/index.js';

describe('redactText', () => {
  it('removes Drizzle parameters, Postgres key details, emails, tokens and URL queries', () => {
    expect(redactText('Failed query: select 1 where email = $1\nparams: a@b.co,secret')).toBe(
      'Failed query: select 1 where email = $1\nparams: [redacted]',
    );
    expect(redactText('Key (email)=(a@b.co) already exists')).toBe(
      'Key (email)=([redacted]) already exists',
    );
    expect(redactText('mail a@b.co failed')).toBe('mail [email] failed');
    expect(redactText('token=abc123 and Bearer xyz')).toBe(
      'token=[redacted] and Bearer [redacted]',
    );
    expect(redactText('GET https://x.test/auth/confirm?token_hash=t&type=recovery failed')).toBe(
      'GET https://x.test/auth/confirm?[redacted] failed',
    );
  });

  it('caps very long text', () => {
    expect(redactText('x'.repeat(5000)).length).toBeLessThanOrEqual(1001);
  });
});

describe('stripQuery', () => {
  it('drops query and fragment', () => {
    expect(stripQuery('/auth/confirm?token_hash=t#x')).toBe('/auth/confirm');
    expect(stripQuery('https://a.test/p#frag')).toBe('https://a.test/p');
    expect(stripQuery('/plain')).toBe('/plain');
  });
});

describe('scrubBreadcrumb', () => {
  it('drops console breadcrumbs', () => {
    expect(scrubBreadcrumb({ category: 'console', message: 'a@b.co' })).toBeNull();
  });

  it('strips http query/fragment data and URL queries', () => {
    const out = scrubBreadcrumb({
      category: 'fetch',
      data: {
        url: 'https://maps.test/api?key=SECRET',
        'http.query': 'key=SECRET',
        'http.fragment': 'f',
        method: 'GET',
      },
    });
    expect(out?.data).toEqual({ url: 'https://maps.test/api', method: 'GET' });
  });

  it('strips navigation from/to queries', () => {
    const out = scrubBreadcrumb({
      category: 'navigation',
      data: { from: '/a?x=1', to: '/auth/confirm?token_hash=t' },
    });
    expect(out?.data).toEqual({ from: '/a', to: '/auth/confirm' });
  });
});

describe('scrubEvent', () => {
  it('keeps only allowlisted headers and strips every URL, body, extra and user field', () => {
    const event: ErrorEvent = {
      type: undefined,
      message: 'failed for a@b.co',
      request: {
        url: 'https://app.test/auth/confirm?token_hash=t&type=recovery',
        headers: {
          Authorization: 'Bearer t',
          cookie: 'sb=1',
          'stripe-signature': 's',
          apikey: 'k',
          'user-agent': 'ua',
          referer: 'https://app.test/login?next=/x',
        },
        cookies: { sb: '1' },
        data: { email: 'a@b.co' },
        query_string: 'token_hash=t',
      },
      contexts: { nextjs: { request_path: '/auth/confirm?token_hash=t', router_kind: 'App' } },
      exception: { values: [{ type: 'DrizzleQueryError', value: 'x\nparams: a@b.co' }] },
      breadcrumbs: [
        { category: 'console', message: 'debug a@b.co' },
        { category: 'http', data: { url: '/app/me?session=s' } },
      ],
      extra: { body: { email: 'a@b.co' } },
      user: { id: 'u1', email: 'a@b.co', ip_address: '1.2.3.4' },
    };
    const out = scrubEvent(event);
    expect(out.request).toEqual({
      url: 'https://app.test/auth/confirm',
      headers: { 'user-agent': 'ua', referer: 'https://app.test/login' },
    });
    expect(out.contexts?.nextjs).toEqual({ request_path: '/auth/confirm', router_kind: 'App' });
    expect(out.exception?.values?.[0]?.value).toBe('x\nparams: [redacted]');
    expect(out.message).toBe('failed for [email]');
    expect(out.breadcrumbs).toEqual([{ category: 'http', data: { url: '/app/me' } }]);
    expect(out.extra).toBeUndefined();
    expect(out.user).toEqual({ id: 'u1' });
    expect(JSON.stringify(out)).not.toMatch(/a@b\.co|token_hash|Bearer|sb=1/);
  });
});

describe('baseSentryOptions', () => {
  it('never sends PII or trace headers, and only enables tracing for a positive rate', () => {
    const off = baseSentryOptions({ dsn: 'https://k@o.ingest.sentry.io/1', environment: 'test' });
    expect(off).toMatchObject({ sendDefaultPii: false, tracePropagationTargets: [] });
    expect('tracesSampleRate' in off).toBe(false);
    const zero = baseSentryOptions({ dsn: 'd', environment: 'e', tracesSampleRate: 0 });
    expect('tracesSampleRate' in zero).toBe(false);
    const on = baseSentryOptions({ dsn: 'd', environment: 'e', tracesSampleRate: 0.1 });
    expect(on.tracesSampleRate).toBe(0.1);
  });
});

describe('redactText code rule', () => {
  it('redacts OAuth codes but keeps status codes readable', () => {
    expect(redactText('callback code=abc123&state=x')).toBe('callback code=[redacted]&state=x');
    expect(redactText('upstream status code: 503')).toBe('upstream status code: 503');
  });
});
