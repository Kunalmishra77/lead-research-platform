import 'reflect-metadata';

import { createApp } from './app.factory';
import { AppModule } from './app.module';
import { initSentry } from './common/observability/sentry';
import { APP_CONFIG } from './config/config.module';
import { type AppConfig, parseEnv } from './config/env.schema';
import { loadEnvironment } from './config/load-env';
import { setupOpenApi } from './openapi';

async function bootstrap(): Promise<void> {
  // Config is parsed during DI (ConfigModule), so loading .env here is early enough.
  loadEnvironment();
  // The adapter is built before DI, so read TRUST_PROXY (and Sentry) through the same parser.
  const env = parseEnv(process.env);
  initSentry(env);
  const app = await createApp(AppModule, env.TRUST_PROXY);
  const config = app.get<AppConfig>(APP_CONFIG);
  if (config.NODE_ENV !== 'production') setupOpenApi(app);
  await app.listen(config.PORT, config.HOST);
}

bootstrap().catch((err: unknown) => {
  // The Nest logger may not exist yet; write one structured line and exit non-zero.
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(
    `${JSON.stringify({ level: 'fatal', msg: 'API failed to start', err: message })}\n`,
  );
  process.exit(1);
});
