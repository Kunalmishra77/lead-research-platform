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

/**
 * Server Component, route handler and server action errors.
 *
 * This used to begin `if (!process.env.SENTRY_DSN_WEB) return`, so with Sentry off — which is how
 * the Coolify deploy runs — every server error was thrown away. React shows the browser a digest
 * and withholds the message in production (error #441), and nothing on the server wrote it down,
 * so a 500 on the job page was a number and nothing else. Two days went into guessing at it.
 *
 * So it always prints, and sends to Sentry as well when there is somewhere to send it. The digest
 * is the first field because it is the only thing the person reporting the fault can see.
 *
 * What is logged: the digest, the route, and the error. Not headers and not the query string —
 * docs/10 keeps a session cookie and a search term out of logs, and neither helps here.
 */
export const onRequestError: Instrumentation.onRequestError = async (error, request, context) => {
  const detail = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const digest =
    typeof error === 'object' && error !== null && 'digest' in error
      ? String(error.digest)
      : 'none';

  console.error(
    `[server error] digest=${digest} route=${context.routePath} (${context.routeType})` +
      ` method=${request.method} path=${request.path.split('?')[0]}\n${detail}`,
  );

  if (process.env.SENTRY_DSN_WEB) {
    const Sentry = await import('@sentry/nextjs');
    Sentry.captureRequestError(error, request, context);
  }
};
