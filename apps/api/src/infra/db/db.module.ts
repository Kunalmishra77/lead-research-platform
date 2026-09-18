import { Global, Inject, Module, type OnApplicationShutdown } from '@nestjs/common';
import postgres from 'postgres';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';

/** Raw postgres.js client (role app_api via the Supabase transaction pooler). Drizzle wraps it in 1.6. */
export const SQL = Symbol('SQL');
export type Sql = postgres.Sql;

@Global()
@Module({
  providers: [
    {
      provide: SQL,
      inject: [APP_CONFIG],
      useFactory: (config: AppConfig): Sql =>
        postgres(config.DATABASE_URL, {
          // Transaction pooler: no server-side prepared statements.
          prepare: false,
          max: config.DB_POOL_MAX,
          idle_timeout: 20,
          connect_timeout: 5,
          connection: { application_name: 'leadforge-api' },
          onnotice: () => undefined,
        }),
    },
  ],
  exports: [SQL],
})
export class DbModule implements OnApplicationShutdown {
  constructor(@Inject(SQL) private readonly sql: Sql) {}

  async onApplicationShutdown(): Promise<void> {
    await this.sql.end({ timeout: 5 });
  }
}
