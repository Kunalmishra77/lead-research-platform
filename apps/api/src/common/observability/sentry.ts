import { baseSentryOptions } from '@leadforge/observability';
import * as Sentry from '@sentry/nestjs';

import type { AppConfig } from '../../config/env.schema';
import type { ErrorClass } from '../errors/app-error';

/**
 * Initialises Sentry when SENTRY_DSN_API is set; otherwise a no-op (captures are dropped).
 * Runs in main.ts before the app is created. Only manual capture (the problem-details filter) is
 * used, so the SDK's auto-instrumentation not seeing earlier imports does not matter.
 * Events are scrubbed by @leadforge/observability (URLs, breadcrumbs, error text, headers).
 */
export function initSentry(config: AppConfig): boolean {
  if (!config.SENTRY_DSN_API) return false;
  Sentry.init(
    baseSentryOptions({
      dsn: config.SENTRY_DSN_API,
      environment: config.SENTRY_ENVIRONMENT ?? config.NODE_ENV,
      tracesSampleRate: config.SENTRY_TRACES_SAMPLE_RATE,
    }),
  );
  return true;
}

/** Outages of dependencies are expected and already classified; they are logged, not reported. */
const NOT_REPORTED: ReadonlySet<ErrorClass | undefined> = new Set(['transient', 'rate_limited']);

export function shouldReport(unexpected: boolean, errorClass: ErrorClass | undefined): boolean {
  return unexpected && !NOT_REPORTED.has(errorClass);
}

/** Reports an unexpected (5xx) error with ids only; a no-op when Sentry is not initialised. */
export function reportError(
  err: unknown,
  context: { requestId: string; code: string; userId?: string; orgId?: string },
): void {
  Sentry.withScope((scope) => {
    scope.setTag('request_id', context.requestId);
    scope.setTag('code', context.code);
    if (context.orgId) scope.setTag('org_id', context.orgId);
    if (context.userId) scope.setUser({ id: context.userId });
    Sentry.captureException(err);
  });
}
