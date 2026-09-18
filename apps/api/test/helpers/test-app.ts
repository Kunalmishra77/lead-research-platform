import { Body, Controller, Get, HttpCode, Post } from '@nestjs/common';
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { Test } from '@nestjs/testing';
import { createZodDto } from 'nestjs-zod';
import { z } from 'zod';

import { configureApp, createAdapter } from '../../src/app.factory';
import { AppModule } from '../../src/app.module';
import { AppError } from '../../src/common/errors/app-error';
import { SQL } from '../../src/infra/db/db.module';
import { REDIS } from '../../src/infra/redis/redis.module';
import { S3 } from '../../src/infra/storage/storage.module';
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

export async function createTestApp(fakes: Fakes): Promise<NestFastifyApplication> {
  Object.assign(process.env, TEST_ENV);
  const fail = (): Promise<never> => Promise.reject(new Error('down'));
  const sql = Object.assign(
    () =>
      Object.assign(fakes.dbOk ? Promise.resolve([{ ok: 1 }]) : fail(), {
        cancel: () => undefined,
      }),
    { end: () => Promise.resolve() },
  );
  const redis = {
    status: 'ready',
    ping: () => (fakes.redisOk ? Promise.resolve('PONG') : fail()),
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

  const moduleRef = await Test.createTestingModule({
    imports: [AppModule],
    controllers: [ProbeController],
  })
    .overrideProvider(SQL)
    .useValue(sql)
    .overrideProvider(REDIS)
    .useValue(redis)
    .overrideProvider(S3)
    .useValue(s3)
    .compile();

  const app = moduleRef.createNestApplication<NestFastifyApplication>(createAdapter(), {
    bufferLogs: true,
  });
  configureApp(app);
  setupOpenApi(app);
  await app.init();
  await app.getHttpAdapter().getInstance().ready();
  return app;
}
