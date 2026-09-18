import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';
import { PinoLogger } from 'nestjs-pino';

import { REDIS } from '../../infra/redis/redis.module';
import type { AuthUser } from '../auth/auth.types';
import { AuditService, type RequestOrigin } from './audit.service';

/**
 * Marker lifetime. A Supabase session still in use after 30 days is recorded once more; far rarer
 * than a daily marker, which would look like a new login every day.
 */
const MARKER_TTL_SECONDS = 30 * 24 * 60 * 60;

/**
 * Login audit (task 1.13): Supabase Auth performs the sign-in, so the API records
 * `auth.session_started` the first time it sees a session (Redis SET NX marker).
 * Runs detached from the request: it never adds latency and never fails a request. When Redis is
 * not ready the event is skipped and logged; errors are logged with their class (no silent drop).
 */
@Injectable()
export class SessionAuditService {
  constructor(
    @Inject(REDIS) private readonly redis: Redis,
    private readonly audit: AuditService,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(SessionAuditService.name);
  }

  /** Fire-and-forget entry point for the AuthGuard. */
  track(user: AuthUser, origin: RequestOrigin): void {
    void this.onAuthenticated(user, origin);
  }

  /** Awaitable variant (tests). Never throws. */
  async onAuthenticated(user: AuthUser, origin: RequestOrigin): Promise<void> {
    if (this.redis.status !== 'ready') {
      this.logger.warn(
        { error_class: 'transient', user_id: user.userId, redis_status: this.redis.status },
        'session audit skipped: redis not ready',
      );
      return;
    }
    const key = `audit:session:${user.sessionId}`;
    try {
      const first = await this.redis.set(key, '1', 'EX', MARKER_TTL_SECONDS, 'NX');
      if (first !== 'OK') return;
      await this.audit.recordPlatform(
        user.userId,
        'auth.session_started',
        { session_id: user.sessionId },
        origin,
      );
    } catch (err) {
      this.logger.error(
        { err, error_class: 'transient', user_id: user.userId },
        'session audit failed',
      );
      // Drop the marker so a later request retries the audit write (best effort).
      await this.redis.del(key).catch(() => 0);
    }
  }
}
