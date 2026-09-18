import { randomUUID } from 'node:crypto';

import {
  createLocalJWKSet,
  exportJWK,
  generateKeyPair,
  type JWTPayload,
  type JWTVerifyGetKey,
  SignJWT,
} from 'jose';

import { TEST_ENV } from './test-app';

export const TEST_ISSUER = `${TEST_ENV.SUPABASE_URL}/auth/v1`;

export interface TestKeys {
  keySet: JWTVerifyGetKey;
  sign: (claims?: JWTPayload, options?: SignOptions) => Promise<string>;
}

export interface SignOptions {
  issuer?: string;
  audience?: string;
  expiresIn?: string | number;
  /** Sign with a different key than the one in keySet. */
  foreignKey?: boolean;
  /** Omit the exp claim entirely. */
  noExpiry?: boolean;
}

/** An ES256 key pair shaped like Supabase's signing keys, plus a Supabase-like token signer. */
export async function createTestKeys(): Promise<TestKeys> {
  const { publicKey, privateKey } = await generateKeyPair('ES256', { extractable: true });
  const foreign = await generateKeyPair('ES256');
  const jwk = { ...(await exportJWK(publicKey)), kid: 'test-key', alg: 'ES256', use: 'sig' };
  const keySet = createLocalJWKSet({ keys: [jwk] });

  const sign = async (claims: JWTPayload = {}, options: SignOptions = {}): Promise<string> => {
    const jwt = new SignJWT({ role: 'authenticated', session_id: randomUUID(), ...claims })
      .setProtectedHeader({ alg: 'ES256', kid: 'test-key' })
      .setIssuer(options.issuer ?? TEST_ISSUER)
      .setAudience(options.audience ?? 'authenticated')
      .setIssuedAt();
    if (options.noExpiry !== true) jwt.setExpirationTime(options.expiresIn ?? '5m');
    return jwt.sign(options.foreignKey === true ? foreign.privateKey : privateKey);
  };

  return { keySet, sign };
}
