import { type JobEnvelope, validateJobEnvelope } from '@leadforge/contracts';
import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';

import { AppError } from '../../common/errors/app-error';
import { REDIS } from '../redis/redis.module';

export const ENVELOPE_FIELD = 'envelope';
const STREAM_MAXLEN = '100000';

export function streamFor(pool: string): string {
  return `jobs:${pool}`;
}

/** Throws (500, invalid_input) for an envelope the workers would reject: a programming error. */
export function assertValidEnvelope(envelope: JobEnvelope): void {
  const result = validateJobEnvelope(envelope);
  if (!result.ok) {
    throw new AppError({
      code: 'jobs.invalid_envelope',
      httpStatus: 500,
      title: 'Internal server error',
      detail: `refusing to publish an invalid envelope: ${result.errors.join('; ')}`,
      errorClass: 'invalid_input',
    });
  }
}

/**
 * Publishes job envelopes onto Redis Streams (ADR-0001). The envelope is validated against the
 * shared JSON Schema first, so the API can never enqueue something the workers would reject.
 */
@Injectable()
export class JobPublisher {
  constructor(@Inject(REDIS) private readonly redis: Redis) {}

  async publish(pool: string, envelope: JobEnvelope): Promise<string> {
    assertValidEnvelope(envelope);
    try {
      if (this.redis.status === 'wait') await this.redis.connect();
      const id = await this.redis.xadd(
        streamFor(pool),
        'MAXLEN',
        '~',
        STREAM_MAXLEN,
        '*',
        ENVELOPE_FIELD,
        JSON.stringify(envelope),
      );
      if (id === null) throw new Error('XADD returned no id');
      return id;
    } catch (err) {
      throw new AppError({
        code: 'jobs.queue_unavailable',
        httpStatus: 503,
        title: 'Job queue temporarily unavailable',
        errorClass: 'transient',
        cause: err,
      });
    }
  }
}
