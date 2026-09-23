import { createHash } from 'node:crypto';

import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';

import { REDIS } from '../../infra/redis/redis.module';
import { AppError } from '../errors/app-error';

/** docs/05: `Idempotency-Key` is honoured on POST and remembered for 24 h. */
const TTL_SECONDS = 24 * 60 * 60;
/** How long a first request may run before a retry is allowed to start again. */
const IN_FLIGHT_SECONDS = 120;
const IN_FLIGHT = '__in_flight__';

@Injectable()
export class IdempotencyService {
  constructor(@Inject(REDIS) private readonly redis: Redis) {}

  /**
   * Runs `work` at most once per (org, route, key): a retry of a request that already succeeded
   * replays the stored response instead of spending credits again. Keys are scoped per org, and
   * the client's key is hashed, so nothing user-supplied is stored verbatim.
   */
  async run<T>(
    scope: { orgId: string; route: string; key: string | undefined },
    work: () => Promise<T>,
  ): Promise<T> {
    if (!scope.key) return work();
    const hash = createHash('sha256').update(scope.key).digest('hex').slice(0, 32);
    const redisKey = `idem:${scope.orgId}:${scope.route}:${hash}`;
    const claimed = await this.redis.set(redisKey, IN_FLIGHT, 'EX', IN_FLIGHT_SECONDS, 'NX');
    if (claimed !== 'OK') {
      const stored = await this.redis.get(redisKey);
      if (stored !== null && stored !== IN_FLIGHT) return JSON.parse(stored) as T;
      throw new AppError({
        code: 'idempotency.in_progress',
        httpStatus: 409,
        title: 'The same request is still running',
        detail: 'Retry once the first request has finished.',
        errorClass: 'transient',
      });
    }
    try {
      const result = await work();
      await this.redis.set(redisKey, JSON.stringify(result), 'EX', TTL_SECONDS);
      return result;
    } catch (err) {
      // A failed attempt must not block a retry.
      await this.redis.del(redisKey).catch(() => undefined);
      throw err;
    }
  }
}
