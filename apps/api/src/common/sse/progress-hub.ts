import { Inject, Injectable, type OnApplicationShutdown } from '@nestjs/common';
import type { Redis } from 'ioredis';

import { REDIS } from '../../infra/redis/redis.module';
import { AppError } from '../errors/app-error';

export const MAX_STREAMS_PER_USER = 5;
export const MAX_STREAMS_TOTAL = 500;

type Listener = (message: string) => void;

/**
 * One Redis subscriber connection for all SSE clients of this process: channels are multiplexed
 * and reference-counted, and concurrent streams are capped per user and in total, so clients
 * cannot exhaust Redis connections. ioredis re-subscribes automatically after a reconnect.
 */
@Injectable()
export class ProgressHub implements OnApplicationShutdown {
  private subscriber: Redis | undefined;
  private connecting: Promise<Redis> | undefined;
  private readonly listeners = new Map<string, Set<Listener>>();
  private readonly perUser = new Map<string, number>();
  private total = 0;

  constructor(@Inject(REDIS) private readonly redis: Redis) {}

  /** Reserves a stream slot for `userId`; returns the release function. Throws 429 when full. */
  acquire(userId: string): () => void {
    const current = this.perUser.get(userId) ?? 0;
    if (current >= MAX_STREAMS_PER_USER || this.total >= MAX_STREAMS_TOTAL) {
      throw new AppError({
        code: 'progress.too_many_streams',
        httpStatus: 429,
        title: 'Too many live progress streams',
        detail: 'Close other progress views and try again',
        errorClass: 'rate_limited',
      });
    }
    this.perUser.set(userId, current + 1);
    this.total += 1;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.total -= 1;
      const left = (this.perUser.get(userId) ?? 1) - 1;
      if (left <= 0) this.perUser.delete(userId);
      else this.perUser.set(userId, left);
    };
  }

  /** Calls `listener` for every message on `channel` until the returned function is called. */
  async listen(channel: string, listener: Listener): Promise<() => Promise<void>> {
    const subscriber = await this.connection();
    let set = this.listeners.get(channel);
    if (!set) {
      set = new Set();
      this.listeners.set(channel, set);
      await subscriber.subscribe(channel);
    }
    set.add(listener);
    return async () => {
      const current = this.listeners.get(channel);
      if (!current) return;
      current.delete(listener);
      if (current.size === 0) {
        this.listeners.delete(channel);
        await subscriber.unsubscribe(channel).catch(() => undefined);
      }
    };
  }

  get activeStreams(): number {
    return this.total;
  }

  onApplicationShutdown(): void {
    this.subscriber?.disconnect();
  }

  private connection(): Promise<Redis> {
    this.connecting ??= (async () => {
      const subscriber = this.redis.duplicate({ lazyConnect: true });
      subscriber.on('message', (channel: string, message: string) => {
        this.listeners.get(channel)?.forEach((listener) => {
          listener(message);
        });
      });
      await subscriber.connect();
      this.subscriber = subscriber;
      return subscriber;
    })().catch((err: unknown) => {
      this.connecting = undefined; // allow a later retry
      throw err;
    });
    return this.connecting;
  }
}
