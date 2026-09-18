import { Body, Controller, Get, HttpCode, Post, type Type } from '@nestjs/common';
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { Test } from '@nestjs/testing';
import type { JWTVerifyGetKey } from 'jose';
import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

import { configureApp, createAdapter } from '../../src/app.factory';
import { AppModule } from '../../src/app.module';
import { AppError } from '../../src/common/errors/app-error';
import { DB, SQL } from '../../src/infra/db/db.module';
import { REDIS } from '../../src/infra/redis/redis.module';
import { S3 } from '../../src/infra/storage/storage.module';
import { Public } from '../../src/modules/auth/public.decorator';
import { JWT_KEY_SET } from '../../src/modules/auth/token-verifier';
import { setupOpenApi } from '../../src/openapi';

export const TEST_ENV = {
  NODE_ENV: 'test',
  LOG_LEVEL: 'silent',
  APP_URL: 'http://localhost:3000',
  DATABASE_URL: 'postgresql://app_api:x@127.0.0.1:1/postgres',
  REDIS_URL: 'redis://127.0.0.1:1/0',
  S3_ENDPOINT: 'http://127.0.0.1:1',
  S3_REGION: 'ap-south-1',
  S3_BUCKET_RAW: 'lf-raw',
  S3_BUCKET_EXPORTS: 'lf-exports',
  S3_ACCESS_KEY_ID: 'test',
  S3_SECRET_ACCESS_KEY: 'test',
  SUPABASE_URL: 'https://example.supabase.co',
  SUPABASE_JWKS_URL: 'https://example.supabase.co/auth/v1/.well-known/jwks.json',
} as const;

export interface Fakes {
  dbOk: boolean;
  redisOk: boolean;
  storageOk: boolean;
  /** Storage never answers; the call only ends when its abortSignal fires. */
  storageHangs?: boolean;
  onStorageAbort?: () => void;
}

const EchoSchema = z.object({ name: z.string().min(2), count: z.number().int().positive() });
class EchoDto extends createZodDto(EchoSchema) {}

/** Test-only routes that exercise validation and error mapping through the real pipeline. */
@Public()
@Controller('__probe')
class ProbeController {
  @Post('echo')
  @HttpCode(200)
  echo(@Body() body: EchoDto): EchoDto {
    return body;
  }

  @Get('app-error')
  appError(): never {
    throw new AppError({
      code: 'research.not_found',
      httpStatus: 404,
      title: 'Research job not found',
      detail: 'No research job with that id in this workspace',
    });
  }

  @Get('crash')
  crash(): never {
    throw new Error('secret internal detail');
  }
}

export interface TestAppOptions {
  /** Verifies test tokens (defaults to a key set that rejects everything). */
  keySet?: JWTVerifyGetKey;
  /** Use the real database from DATABASE_URL instead of fakes (live tests only). */
  realDb?: boolean;
  /** Use the real Redis from REDIS_URL instead of the in-memory fake (live tests only). */
  realRedis?: boolean;
  controllers?: Type[];
}

const rejectAllKeys: JWTVerifyGetKey = () => Promise.reject(new Error('no test key set'));

export async function createTestApp(
  fakes: Fakes,
  options: TestAppOptions = {},
): Promise<NestFastifyApplication> {
  const realDbUrl = process.env.DATABASE_URL;
  const realRedisUrl = process.env.REDIS_URL;
  Object.assign(process.env, TEST_ENV);
  if (options.realDb === true) {
    if (!realDbUrl) throw new Error('realDb requires DATABASE_URL');
    process.env.DATABASE_URL = realDbUrl;
  }
  if (options.realRedis === true) {
    if (!realRedisUrl) throw new Error('realRedis requires REDIS_URL');
    process.env.REDIS_URL = realRedisUrl;
  }
  const fail = (): Promise<never> => Promise.reject(new Error('down'));
  const sql = Object.assign(
    () =>
      Object.assign(fakes.dbOk ? Promise.resolve([{ ok: 1 }]) : fail(), {
        cancel: () => undefined,
      }),
    { end: () => Promise.resolve() },
  );
  const kv = new Map<string, string>();
  const redis = {
    status: 'ready',
    ping: () => (fakes.redisOk ? Promise.resolve('PONG') : fail()),
    // SET key value EX ttl NX (session-audit markers)
    set: (key: string, value: string, ...args: unknown[]) => {
      if (args.includes('NX') && kv.has(key)) return Promise.resolve(null);
      kv.set(key, value);
      return Promise.resolve('OK');
    },
    del: (key: string) => Promise.resolve(kv.delete(key) ? 1 : 0),
    connect: () => Promise.resolve(),
    quit: () => Promise.resolve('OK'),
    disconnect: () => undefined,
  };
  const s3 = {
    send: (_command: unknown, options?: { abortSignal?: AbortSignal }) => {
      if (fakes.storageHangs) {
        return new Promise((_resolve, reject) => {
          options?.abortSignal?.addEventListener('abort', () => {
            fakes.onStorageAbort?.();
            reject(new Error('aborted'));
          });
        });
      }
      return fakes.storageOk ? Promise.resolve({}) : fail();
    },
    destroy: () => undefined,
  };

  let builder = Test.createTestingModule({
    imports: [AppModule],
    controllers: [ProbeController, ...(options.controllers ?? [])],
  })
    .overrideProvider(JWT_KEY_SET)
    .useValue(options.keySet ?? rejectAllKeys)
    .overrideProvider(S3)
    .useValue(s3);
  if (options.realRedis !== true) builder = builder.overrideProvider(REDIS).useValue(redis);
  if (options.realDb !== true) {
    // Skeleton routes never use Drizzle; live tests pass realDb instead.
    builder = builder.overrideProvider(SQL).useValue(sql).overrideProvider(DB).useValue({});
  }
  const moduleRef = await builder.compile();

  const app = moduleRef.createNestApplication<NestFastifyApplication>(createAdapter(), {
    bufferLogs: true,
  });
  configureApp(app);
  setupOpenApi(app);
  await app.init();
  await app.getHttpAdapter().getInstance().ready();
  return app;
}
