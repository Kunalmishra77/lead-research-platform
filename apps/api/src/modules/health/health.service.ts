import { HeadBucketCommand, type S3Client } from '@aws-sdk/client-s3';
import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import { SQL, type Sql } from '../../infra/db/db.module';
import { REDIS } from '../../infra/redis/redis.module';
import { S3 } from '../../infra/storage/storage.module';

export type DependencyName = 'db' | 'redis' | 'storage';
export type CheckResult = 'ok' | 'fail';

export interface Readiness {
  status: 'ok' | 'fail';
  checks: Record<DependencyName, CheckResult>;
}

export const CHECK_TIMEOUT_MS = 3000;

/** Runs `probe` with an AbortSignal that fires after CHECK_TIMEOUT_MS; the probe must honour it. */
async function withDeadline(probe: (signal: AbortSignal) => Promise<unknown>): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new Error('timeout'));
  }, CHECK_TIMEOUT_MS);
  try {
    await Promise.race([
      probe(controller.signal),
      new Promise((_resolve, reject) => {
        controller.signal.addEventListener('abort', () => {
          reject(new Error('timeout'));
        });
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

@Injectable()
export class HealthService {
  private redisConnect: Promise<void> | null = null;

  constructor(
    @Inject(SQL) private readonly sql: Sql,
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(S3) private readonly s3: S3Client,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  async readiness(): Promise<Readiness> {
    const probes: Record<DependencyName, (signal: AbortSignal) => Promise<unknown>> = {
      db: (signal) => {
        const query = this.sql`select 1`;
        signal.addEventListener('abort', () => {
          query.cancel();
        });
        return query;
      },
      redis: async () => {
        await this.ensureRedisConnected();
        return this.redis.ping();
      },
      storage: (signal) =>
        this.s3.send(new HeadBucketCommand({ Bucket: this.config.S3_BUCKET_RAW }), {
          abortSignal: signal,
        }),
    };
    const names = Object.keys(probes) as DependencyName[];
    const results = await Promise.allSettled(names.map((n) => withDeadline(probes[n])));
    const checks = Object.fromEntries(
      names.map((n, i) => [n, results[i]?.status === 'fulfilled' ? 'ok' : 'fail']),
    ) as Record<DependencyName, CheckResult>;
    return { status: Object.values(checks).every((c) => c === 'ok') ? 'ok' : 'fail', checks };
  }

  /** lazyConnect client: connect once even when probes overlap. */
  private async ensureRedisConnected(): Promise<void> {
    if (this.redis.status !== 'wait') return;
    this.redisConnect ??= this.redis.connect().finally(() => {
      this.redisConnect = null;
    });
    await this.redisConnect;
  }
}
