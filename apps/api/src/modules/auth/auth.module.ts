import { Global, Module } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { createRemoteJWKSet, type JWTVerifyGetKey } from 'jose';

import { TenantGuard } from '../../common/rbac/tenant.guard';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import { AuthGuard } from './auth.guard';
import { JWT_KEY_SET, TokenVerifier } from './token-verifier';

@Global()
@Module({
  providers: [
    {
      provide: JWT_KEY_SET,
      inject: [APP_CONFIG],
      useFactory: (config: AppConfig): JWTVerifyGetKey =>
        createRemoteJWKSet(new URL(config.SUPABASE_JWKS_URL), {
          timeoutDuration: 5000,
          cooldownDuration: 30_000,
          cacheMaxAge: 10 * 60_000,
        }),
    },
    TokenVerifier,
    TenantGuard,
    { provide: APP_GUARD, useClass: AuthGuard },
  ],
  exports: [TokenVerifier, TenantGuard],
})
export class AuthModule {}
