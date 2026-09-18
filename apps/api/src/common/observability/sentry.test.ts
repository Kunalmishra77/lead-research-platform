import { describe, expect, it } from 'vitest';

import { TEST_ENV } from '../../../test/helpers/test-app';
import { parseEnv } from '../../config/env.schema';
import { initSentry, reportError, shouldReport } from './sentry';

describe('initSentry', () => {
  it('stays off without a DSN, treats an empty value as unset, and reporting is a no-op', () => {
    expect(initSentry(parseEnv({ ...TEST_ENV }))).toBe(false);
    expect(initSentry(parseEnv({ ...TEST_ENV, SENTRY_DSN_API: '' }))).toBe(false);
    expect(() => {
      reportError(new Error('boom'), { requestId: 'r', code: 'internal_error' });
    }).not.toThrow();
  });

  it('rejects an invalid DSN and sample rate', () => {
    expect(() => parseEnv({ ...TEST_ENV, SENTRY_DSN_API: 'not a url' })).toThrow(/SENTRY_DSN_API/);
    expect(() => parseEnv({ ...TEST_ENV, SENTRY_TRACES_SAMPLE_RATE: '2' })).toThrow(
      /SENTRY_TRACES_SAMPLE_RATE/,
    );
  });
});

describe('shouldReport', () => {
  it('reports unexpected errors except classified dependency outages', () => {
    expect(shouldReport(true, undefined)).toBe(true);
    expect(shouldReport(true, 'parse_failed')).toBe(true);
    expect(shouldReport(true, 'transient')).toBe(false);
    expect(shouldReport(true, 'rate_limited')).toBe(false);
    expect(shouldReport(false, undefined)).toBe(false);
  });
});
