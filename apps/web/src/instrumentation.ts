import type { Instrumentation } from 'next';

/** Runs once per server process (Next.js instrumentation hook). */
export async function register(): Promise<void> {
  // Node runtime only: the edge runtime is not used (proxy.ts runs on Node), so Sentry is not
  // initialised there and onRequestError below is a no-op for edge requests.
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    // Dev-only DoH for *.supabase.co (ADR-0002); a no-op unless DEV_DNS_OVER_HTTPS=true.
    const { installDevDnsFromEnv } = await import('@leadforge/dev-dns');
    installDevDnsFromEnv(process.env);

    // Error reporting (task 1.15): off unless SENTRY_DSN_WEB is set. Events are scrubbed by
    // @leadforge/observability (URLs incl. request_path, breadcrumbs, error text, headers).
    const dsn = process.env.SENTRY_DSN_WEB;
    if (dsn) {
      const Sentry = await import('@sentry/nextjs');
      const { baseSentryOptions } = await import('@leadforge/observability');
      Sentry.init(
        baseSentryOptions({
          dsn,
          environment: process.env.SENTRY_ENVIRONMENT || process.env.NODE_ENV,
          tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? 0),
        }),
      );
    }
  }
}

/** Server Component, route handler and server action errors; dropped when Sentry is off. */
export const onRequestError: Instrumentation.onRequestError = async (...args) => {
  if (!process.env.SENTRY_DSN_WEB) return;
  const Sentry = await import('@sentry/nextjs');
  Sentry.captureRequestError(...args);
};
