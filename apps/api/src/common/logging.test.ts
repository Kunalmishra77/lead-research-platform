import { Writable } from 'node:stream';

import pino from 'pino';
import { describe, expect, it } from 'vitest';

import { REDACTED_PATHS, serializeRequest } from './logging';

describe('serializeRequest', () => {
  it('drops the query string (it may carry OAuth codes or tokens)', () => {
    const out = serializeRequest({
      id: 'r1',
      method: 'GET',
      url: '/integrations/google/callback?code=SECRET&state=x',
      headers: {},
    });
    expect(out.path).toBe('/integrations/google/callback');
    expect(JSON.stringify(out)).not.toContain('SECRET');
  });

  it('prefers originalUrl (Fastify middie rewrites url)', () => {
    expect(serializeRequest({ url: '/', originalUrl: '/health/ready?x=1', headers: {} }).path).toBe(
      '/health/ready',
    );
  });
});

describe('REDACTED_PATHS', () => {
  it('censors credentials headers in log output', () => {
    const lines: string[] = [];
    const sink = new Writable({
      write(chunk: Buffer, _enc, done) {
        lines.push(chunk.toString());
        done();
      },
    });
    const logger = pino({ redact: { paths: REDACTED_PATHS, censor: '[redacted]' } }, sink);
    logger.info({
      req: {
        headers: {
          authorization: 'Bearer eyJsecret',
          cookie: 'sb=secret',
          'x-api-key': 'lf_live_secret',
          'proxy-authorization': 'Basic secret',
          accept: 'application/json',
        },
      },
    });
    const line = lines.join('');
    expect(line).not.toContain('secret');
    expect(line).toContain('application/json');
  });
});
