// Browser error reporting (task 1.15). NEXT_PUBLIC_SENTRY_DSN is inlined at build time from
// SENTRY_DSN_WEB (next.config.ts); when unset the SDK is never loaded. Events and breadcrumbs
// (console dropped; fetch/navigation URLs without query strings) are scrubbed before sending.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  void Promise.all([import('@sentry/nextjs'), import('@leadforge/observability')]).then(
    ([Sentry, { baseSentryOptions }]) => {
      Sentry.init(
        baseSentryOptions({
          dsn,
          environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || process.env.NODE_ENV,
        }),
      );
    },
  );
}
