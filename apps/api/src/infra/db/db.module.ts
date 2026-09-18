import { createDb, type Database } from '@leadforge/db';
import { Global, Inject, Module, type OnApplicationShutdown } from '@nestjs/common';
import postgres from 'postgres';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';

/** Raw postgres.js client (role app_api via the Supabase transaction pooler). */
export const SQL = Symbol('SQL');
export type Sql = postgres.Sql;

/**
 * Drizzle over the same client. Tenant tables must only be queried inside
 * `withTenant(db, { orgId, userId }, tx => ...)` from @leadforge/db (RLS reads that context).
 */
export const DB = Symbol('DB');
export type { Database };

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
    { provide: DB, inject: [SQL], useFactory: (sql: Sql): Database => createDb(sql) },
  ],
  exports: [SQL, DB],
})
export class DbModule implements OnApplicationShutdown {
  constructor(@Inject(SQL) private readonly sql: Sql) {}

  async onApplicationShutdown(): Promise<void> {
    await this.sql.end({ timeout: 5 });
  }
}
