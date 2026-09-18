import { SignJWT } from 'jose';
import { beforeAll, describe, expect, it } from 'vitest';

import { TEST_ENV } from '../../../test/helpers/test-app';
import { createTestKeys, type TestKeys } from '../../../test/helpers/tokens';
import { parseEnv } from '../../config/env.schema';
import { TokenVerifier } from './token-verifier';

const USER = '01923f4e-7b3a-7c2d-9f10-0000000000aa';

describe('TokenVerifier', () => {
  let keys: TestKeys;
  let verifier: TokenVerifier;

  beforeAll(async () => {
    keys = await createTestKeys();
    verifier = new TokenVerifier(keys.keySet, parseEnv(TEST_ENV));
  });

  const rejectsWith = async (token: string, code: string) => {
    await expect(verifier.verify(token)).rejects.toMatchObject({ code });
  };

  it('accepts a valid Supabase access token', async () => {
    const session = '01923f4e-7b3a-7c2d-9f10-00000000beef';
    const token = await keys.sign({ sub: USER, email: 'a@example.com', session_id: session });
    await expect(verifier.verify(token)).resolves.toEqual({
      userId: USER,
      email: 'a@example.com',
      sessionId: session,
    });
  });

  it('rejects tokens without exp (they would never expire)', async () => {
    await rejectsWith(await keys.sign({ sub: USER }, { noExpiry: true }), 'auth.invalid_token');
  });

  it('rejects tokens without a UUID session id', async () => {
    await rejectsWith(await keys.sign({ sub: USER, session_id: 'nope' }), 'auth.invalid_token');
  });

  it('maps JWKS outages to 503 transient, not an invalid token', async () => {
    const outage = new TokenVerifier(
      () => Promise.reject(new Error('Expected 200 OK from the JSON Web Key Set HTTP response')),
      parseEnv(TEST_ENV),
    );
    await expect(outage.verify(await keys.sign({ sub: USER }))).rejects.toMatchObject({
      code: 'auth.unavailable',
      httpStatus: 503,
      errorClass: 'transient',
    });
  });

  it('rejects expired tokens (beyond clock tolerance)', async () => {
    await rejectsWith(await keys.sign({ sub: USER }, { expiresIn: '-2m' }), 'auth.invalid_token');
  });

  it('rejects another issuer', async () => {
    await rejectsWith(
      await keys.sign({ sub: USER }, { issuer: 'https://evil.supabase.co/auth/v1' }),
      'auth.invalid_token',
    );
  });

  it('rejects another audience', async () => {
    await rejectsWith(await keys.sign({ sub: USER }, { audience: 'anon' }), 'auth.invalid_token');
  });

  it('rejects tokens signed by an unknown key', async () => {
    await rejectsWith(await keys.sign({ sub: USER }, { foreignKey: true }), 'auth.invalid_token');
  });

  it('rejects symmetric (HS256) tokens even with a matching kid', async () => {
    const token = await new SignJWT({ sub: USER, role: 'authenticated' })
      .setProtectedHeader({ alg: 'HS256', kid: 'test-key' })
      .setIssuer(`${TEST_ENV.SUPABASE_URL}/auth/v1`)
      .setAudience('authenticated')
      .setExpirationTime('5m')
      .sign(new TextEncoder().encode('x'.repeat(32)));
    await rejectsWith(token, 'auth.invalid_token');
  });

  it('rejects anon-role and anonymous-user tokens', async () => {
    await rejectsWith(await keys.sign({ sub: USER, role: 'anon' }), 'auth.invalid_token');
    await rejectsWith(await keys.sign({ sub: USER, is_anonymous: true }), 'auth.invalid_token');
  });

  it('rejects tokens without a UUID subject', async () => {
    await rejectsWith(await keys.sign({ sub: 'not-a-uuid' }), 'auth.invalid_token');
    await rejectsWith(await keys.sign({}), 'auth.invalid_token');
  });

  it('rejects garbage', async () => {
    await rejectsWith('a.b.c', 'auth.invalid_token');
  });
});
