import { S3Client } from '@aws-sdk/client-s3';
import { Global, Inject, Module, type OnApplicationShutdown } from '@nestjs/common';

import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';

/** S3-compatible client (Supabase Storage in dev, ADR-0002). */
export const S3 = Symbol('S3');

@Global()
@Module({
  providers: [
    {
      provide: S3,
      inject: [APP_CONFIG],
      useFactory: (config: AppConfig): S3Client =>
        new S3Client({
          endpoint: config.S3_ENDPOINT,
          region: config.S3_REGION,
          forcePathStyle: true,
          // Bound socket lifetime so a slow or unreachable endpoint cannot pile up requests.
          requestHandler: { connectionTimeout: 3000, requestTimeout: 10_000 },
          credentials: {
            accessKeyId: config.S3_ACCESS_KEY_ID,
            secretAccessKey: config.S3_SECRET_ACCESS_KEY,
          },
        }),
    },
  ],
  exports: [S3],
})
export class StorageModule implements OnApplicationShutdown {
  constructor(@Inject(S3) private readonly s3: S3Client) {}

  onApplicationShutdown(): void {
    this.s3.destroy();
  }
}
