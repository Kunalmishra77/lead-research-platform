import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';
import { uuidv7 } from 'uuidv7';

import { AppError } from '../../common/errors/app-error';
import { REDIS } from '../redis/redis.module';

/**
 * Prefix the workers will write to, and nothing else (`app/jobs/rpc.py`). The key is generated
 * here and never taken from a request: a caller-supplied key would let anyone choose where a
 * worker writes in Redis.
 */
export const REPLY_PREFIX = 'rpc:reply:';

export const replyKeyFor = (jobId: string): string => `${REPLY_PREFIX}${jobId}`;

/** BLPOP takes whole seconds, and 0 would mean "wait forever". */
const MIN_TIMEOUT_SECONDS = 1;

/** The connection must outlive the BLPOP it is carrying, not race it. */
const TIMEOUT_HEADROOM_MS = 5_000;

/**
 * Waiting for a worker to answer a synchronous request (ADR-0005).
 *
 * Each wait gets its own connection, and gives it back when it ends. `BLPOP` occupies a
 * connection for its whole duration and ioredis writes commands in order on one socket, so a
 * shared connection would make waits queue behind each other: one org's stuck parse would hold
 * up every other org's. A connection per wait is cheap next to a call that can take 20 seconds.
 */
@Injectable()
export class JobRpc {
  constructor(@Inject(REDIS) private readonly redis: Redis) {}

  newJobId(): string {
    return uuidv7();
  }

  /**
   * Waits for one reply, or null when nobody answered in time, which the caller reports as
   * "no worker available" rather than as a generic timeout: it is almost always that.
   */
  async await(key: string, timeoutMs: number): Promise<unknown> {
    if (!key.startsWith(REPLY_PREFIX)) {
      throw new AppError({
        code: 'jobs.invalid_reply_key',
        httpStatus: 500,
        title: 'Internal server error',
        detail: `refusing to wait on a key outside ${REPLY_PREFIX}`,
        errorClass: 'invalid_input',
      });
    }
    const seconds = Math.max(MIN_TIMEOUT_SECONDS, Math.ceil(timeoutMs / 1000));
    // The shared client sets a 3 s commandTimeout, and `duplicate()` copies every option, which
    // ioredis then applies to BLPOP like any other command. Without this override a 20 s wait
    // rejects after 3 s and the caller is told the queue is down while a worker is still
    // working and still billing.
    const client = this.redis.duplicate({ commandTimeout: timeoutMs + TIMEOUT_HEADROOM_MS });
    try {
      const popped = await client.blpop(key, seconds);
      if (popped === null) {
        // Nothing arrived, so the key may still be waiting for a late reply nobody wants.
        await this.redis.del(key).catch(() => undefined);
        return null;
      }
      return JSON.parse(popped[1]) as unknown;
    } catch (err) {
      if (err instanceof SyntaxError) {
        throw new AppError({
          code: 'jobs.invalid_reply',
          httpStatus: 502,
          title: 'Worker sent an unreadable reply',
          errorClass: 'parse_failed',
          cause: err,
        });
      }
      throw new AppError({
        code: 'jobs.queue_unavailable',
        httpStatus: 503,
        title: 'Job queue temporarily unavailable',
        errorClass: 'transient',
        cause: err,
      });
    } finally {
      client.disconnect();
    }
  }

  /**
   * Whether anything is consuming a pool. A parse that would wait 20 seconds for a worker that
   * was never started should fail in milliseconds instead, saying so.
   */
  async hasConsumers(stream: string, group: string): Promise<boolean> {
    try {
      const groups = (await this.redis.xinfo('GROUPS', stream)) as unknown[][];
      return groups.some((row) => {
        const fields = row as (string | number)[];
        const nameAt = fields.indexOf('name');
        const consumersAt = fields.indexOf('consumers');
        return (
          nameAt >= 0 &&
          fields[nameAt + 1] === group &&
          consumersAt >= 0 &&
          Number(fields[consumersAt + 1]) > 0
        );
      });
    } catch {
      // No stream yet, or Redis said something unexpected: let the wait decide.
      return false;
    }
  }
}
