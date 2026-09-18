import { Inject, Injectable } from '@nestjs/common';
import { errors as joseErrors, jwtVerify, type JWTVerifyGetKey } from 'jose';
import { z } from 'zod';

import { AppError } from '../../common/errors/app-error';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import type { AuthUser } from './auth.types';

/** Key resolver for Supabase access tokens (remote JWKS in the app, a local set in tests). */
export const JWT_KEY_SET = Symbol('JWT_KEY_SET');

const claimsSchema = z.object({
  sub: z.uuid(),
  role: z.literal('authenticated'),
  session_id: z.uuid(),
  email: z.string().optional(),
  is_anonymous: z.boolean().optional(),
});

/** jose errors that mean "this token is not acceptable" (401). Anything else is infrastructure. */
const TOKEN_ERRORS = [
  joseErrors.JWTExpired,
  joseErrors.JWTClaimValidationFailed,
  joseErrors.JWTInvalid,
  joseErrors.JWSInvalid,
  joseErrors.JWSSignatureVerificationFailed,
  joseErrors.JOSEAlgNotAllowed,
  joseErrors.JOSENotSupported,
  joseErrors.JWKSNoMatchingKey,
  joseErrors.JWKSMultipleMatchingKeys,
];

const invalidToken = (detail: string, cause?: unknown) =>
  new AppError({
    code: 'auth.invalid_token',
    httpStatus: 401,
    title: 'Invalid access token',
    detail,
    errorClass: 'invalid_input',
    cause,
  });

@Injectable()
export class TokenVerifier {
  private readonly issuer: string;

  constructor(
    @Inject(JWT_KEY_SET) private readonly keys: JWTVerifyGetKey,
    @Inject(APP_CONFIG) config: AppConfig,
  ) {
    this.issuer = `${config.SUPABASE_URL}/auth/v1`;
  }

  /**
   * Verifies signature (asymmetric only), issuer, audience, expiry and the claims we rely on.
   * Token problems -> 401. JWKS unavailable or malformed -> 503 (transient), so a Supabase blip does
   * not look like an invalid session to callers.
   */
  async verify(token: string): Promise<AuthUser> {
    let payload: unknown;
    try {
      ({ payload } = await jwtVerify(token, this.keys, {
        issuer: this.issuer,
        audience: 'authenticated',
        algorithms: ['ES256', 'RS256'],
        requiredClaims: ['exp', 'iat', 'sub'],
        clockTolerance: 30,
      }));
    } catch (err) {
      if (TOKEN_ERRORS.some((cls) => err instanceof cls)) {
        throw invalidToken('The access token is invalid or expired', err);
      }
      throw new AppError({
        code: 'auth.unavailable',
        httpStatus: 503,
        title: 'Authentication temporarily unavailable',
        errorClass: 'transient',
        cause: err,
      });
    }
    const claims = claimsSchema.safeParse(payload);
    if (!claims.success) throw invalidToken('The access token is missing required claims');
    if (claims.data.is_anonymous === true) throw invalidToken('Anonymous sessions are not allowed');
    return {
      userId: claims.data.sub,
      email: claims.data.email ?? null,
      sessionId: claims.data.session_id,
    };
  }
}
