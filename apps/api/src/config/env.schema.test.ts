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

  it('normalizes SUPABASE_URL (token issuer must match exactly)', () => {
    expect(
      parseEnv({ ...TEST_ENV, SUPABASE_URL: 'https://example.supabase.co/' }).SUPABASE_URL,
    ).toBe('https://example.supabase.co');
  });

  it('requires the JWKS URL on the same origin as SUPABASE_URL', () => {
    expect(() =>
      parseEnv({ ...TEST_ENV, SUPABASE_JWKS_URL: 'https://evil.example.com/jwks.json' }),
    ).toThrow(/SUPABASE_JWKS_URL/);
  });

  it('parses TRUST_PROXY as hops or CIDRs and refuses "true"', () => {
    expect(parseEnv({ ...TEST_ENV }).TRUST_PROXY).toBe(false);
    expect(parseEnv({ ...TEST_ENV, TRUST_PROXY: '1' }).TRUST_PROXY).toBe(1);
    expect(parseEnv({ ...TEST_ENV, TRUST_PROXY: '10.0.0.0/8, 172.16.0.0/12' }).TRUST_PROXY).toEqual(
      ['10.0.0.0/8', '172.16.0.0/12'],
    );
    expect(() => parseEnv({ ...TEST_ENV, TRUST_PROXY: 'true' })).toThrow(/TRUST_PROXY/);
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
