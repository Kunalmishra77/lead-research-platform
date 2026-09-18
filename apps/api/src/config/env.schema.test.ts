import { describe, expect, it } from 'vitest';

import { TEST_ENV } from '../../test/helpers/test-app';
import { parseEnv } from './env.schema';

describe('parseEnv', () => {
  it('applies defaults and coerces types', () => {
    const config = parseEnv({ ...TEST_ENV, PORT: '4100', DEV_DNS_OVER_HTTPS: 'true' });
    expect(config.PORT).toBe(4100);
    expect(config.DEV_DNS_OVER_HTTPS).toBe(true);
    expect(config.HOST).toBe('0.0.0.0');
  });

  it('lists every invalid variable without echoing values', () => {
    const secret = 'hunter2-is-not-a-url';
    const env: NodeJS.ProcessEnv = { ...TEST_ENV, DATABASE_URL: secret, PORT: 'abc' };
    delete env.REDIS_URL;
    expect(() => parseEnv(env)).toThrow(/DATABASE_URL[\s\S]*PORT|PORT[\s\S]*DATABASE_URL/);
    expect(() => parseEnv(env)).toThrow(/REDIS_URL/);
    try {
      parseEnv(env);
    } catch (err) {
      expect(String(err)).not.toContain('hunter2');
    }
  });
});
