import { Module } from '@nestjs/common';
import { APP_FILTER, APP_PIPE } from '@nestjs/core';
import { LoggerModule } from 'nestjs-pino';
import { ZodValidationPipe } from 'nestjs-zod';

import { ProblemDetailsFilter } from './common/errors/problem-details.filter';
import { REDACTED_PATHS, serializeRequest } from './common/logging';
import { REQUEST_ID_HEADER } from './common/request-id';
import { APP_CONFIG, ConfigModule } from './config/config.module';
import type { AppConfig } from './config/env.schema';
import { DbModule } from './infra/db/db.module';
import { RedisModule } from './infra/redis/redis.module';
import { StorageModule } from './infra/storage/storage.module';
import { AuditModule } from './modules/audit/audit.module';
import { AuthModule } from './modules/auth/auth.module';
import { HealthModule } from './modules/health/health.module';
import { MembersModule } from './modules/members/members.module';
import { OrgsModule } from './modules/orgs/orgs.module';

@Module({
  imports: [
    ConfigModule,
    LoggerModule.forRootAsync({
      inject: [APP_CONFIG],
      useFactory: (config: AppConfig) => ({
        pinoHttp: {
          level: config.LOG_LEVEL,
          // Same id Fastify assigned in generateRequestId (it rewrites the raw header).
          genReqId: (req) => String(req.headers[REQUEST_ID_HEADER]),
          customAttributeKeys: { reqId: 'request_id' },
          autoLogging: {
            // Under Fastify, middie rewrites req.url for mounted middleware; originalUrl is the real path.
            ignore: (req) =>
              ((req as { originalUrl?: string }).originalUrl ?? req.url ?? '').startsWith(
                '/health/live',
              ),
          },
          // The std serializer keeps the raw request on `.raw`; log only path + safe fields.
          serializers: {
            req: (req: { raw?: Parameters<typeof serializeRequest>[0] }) =>
              serializeRequest(req.raw ?? (req as Parameters<typeof serializeRequest>[0])),
          },
          redact: { paths: REDACTED_PATHS, censor: '[redacted]' },
          ...(config.NODE_ENV === 'development'
            ? { transport: { target: 'pino-pretty', options: { singleLine: true } } }
            : {}),
        },
      }),
    }),
    DbModule,
    RedisModule,
    StorageModule,
    AuditModule,
    AuthModule,
    HealthModule,
    OrgsModule,
    MembersModule,
  ],
  providers: [
    { provide: APP_PIPE, useClass: ZodValidationPipe },
    { provide: APP_FILTER, useClass: ProblemDetailsFilter },
  ],
})
export class AppModule {}
