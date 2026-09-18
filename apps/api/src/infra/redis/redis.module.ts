import {
  Global,
  Inject,
  Module,
  type OnApplicationBootstrap,
  type OnApplicationShutdown,
} from '@nestjs/common';
import { Redis } from 'ioredis';
import { PinoLogger } from 'nestjs-pino';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';

export const REDIS = Symbol('REDIS');

@Global()
@Module({
  providers: [
    {
      provide: REDIS,
      inject: [APP_CONFIG],
      useFactory: (config: AppConfig): Redis =>
        new Redis(config.REDIS_URL, {
          // Connected in onApplicationBootstrap; ioredis reconnects on its own afterwards.
          lazyConnect: true,
          maxRetriesPerRequest: 2,
          connectTimeout: 3000,
          commandTimeout: 3000,
          connectionName: 'leadforge-api',
        }),
    },
  ],
  exports: [REDIS],
})
export class RedisModule implements OnApplicationBootstrap, OnApplicationShutdown {
  constructor(
    @Inject(REDIS) private readonly redis: Redis,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(RedisModule.name);
  }

  /** Connect at startup without blocking it: readiness reports Redis until it is up. */
  onApplicationBootstrap(): void {
    if (this.redis.status !== 'wait') return;
    this.redis.connect().catch((err: unknown) => {
      this.logger.error({ err, error_class: 'transient' }, 'redis initial connect failed');
    });
  }

  async onApplicationShutdown(): Promise<void> {
    if (this.redis.status === 'ready') await this.redis.quit();
    else this.redis.disconnect();
  }
}
