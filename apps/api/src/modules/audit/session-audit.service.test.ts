import type { Redis } from 'ioredis';
import type { PinoLogger } from 'nestjs-pino';
import { describe, expect, it, vi } from 'vitest';

import type { AuditService } from './audit.service';
import { SessionAuditService } from './session-audit.service';

const USER = { userId: 'u1', email: null, sessionId: 's1' };
const ORIGIN = { ip: '203.0.113.9', userAgent: 'test' };

function setup(redisOverrides: Partial<Record<'status' | 'set' | 'del', unknown>> = {}) {
  const store = new Set<string>();
  const redis = {
    status: 'ready',
    set: vi.fn((key: string) => {
      if (store.has(key)) return Promise.resolve(null);
      store.add(key);
      return Promise.resolve('OK');
    }),
    del: vi.fn((key: string) => Promise.resolve(store.delete(key) ? 1 : 0)),
    ...redisOverrides,
  };
  const audit = { recordPlatform: vi.fn(() => Promise.resolve()) };
  const logger = { setContext: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const service = new SessionAuditService(
    redis as unknown as Redis,
    audit as unknown as AuditService,
    logger as unknown as PinoLogger,
  );
  return { service, redis, audit, logger, store };
}

describe('SessionAuditService', () => {
  it('audits a session once, however many requests it makes', async () => {
    const { service, audit } = setup();
    await service.onAuthenticated(USER, ORIGIN);
    await service.onAuthenticated(USER, ORIGIN);
    expect(audit.recordPlatform).toHaveBeenCalledTimes(1);
    expect(audit.recordPlatform).toHaveBeenCalledWith(
      'u1',
      'auth.session_started',
      { session_id: 's1' },
      ORIGIN,
    );
  });

  it('skips (and logs) when Redis is not ready, without calling it', async () => {
    const { service, redis, audit, logger } = setup({ status: 'reconnecting' });
    await service.onAuthenticated(USER, ORIGIN);
    expect(redis.set).not.toHaveBeenCalled();
    expect(audit.recordPlatform).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalled();
  });

  it('drops the marker when the audit write fails, so the next request retries', async () => {
    const { service, audit, logger, store } = setup();
    audit.recordPlatform.mockRejectedValueOnce(new Error('db down'));
    await service.onAuthenticated(USER, ORIGIN);
    expect(logger.error).toHaveBeenCalled();
    expect(store.size).toBe(0);
    await service.onAuthenticated(USER, ORIGIN);
    expect(audit.recordPlatform).toHaveBeenCalledTimes(2);
  });

  it('never throws, even when Redis fails', async () => {
    const { service } = setup({
      set: vi.fn(() => Promise.reject(new Error('timeout'))),
      del: vi.fn(() => Promise.reject(new Error('timeout'))),
    });
    await expect(service.onAuthenticated(USER, ORIGIN)).resolves.toBeUndefined();
  });
});
