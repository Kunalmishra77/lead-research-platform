import type { Breadcrumb, ErrorEvent } from '@sentry/core';

/**
 * Sentry scrubbing shared by the API and the web app (task 1.15, docs/10 logging hygiene).
 * Nothing that can identify a person or grant access may leave our systems: tokens in query strings
 * (email links, OAuth codes), contact data in error text or SQL parameters, cookies, credentials.
 */

const MAX_TEXT = 1000;

/** Headers worth keeping for debugging; everything else (auth, cookies, signatures, keys) goes. */
const HEADER_ALLOWLIST = new Set([
  'accept',
  'accept-language',
  'content-length',
  'content-type',
  'host',
  'referer',
  'user-agent',
]);

const TEXT_RULES: [RegExp, string][] = [
  // Drizzle: "Failed query: <sql>\nparams: <values>"; the values are user data.
  [/(\bparams:\s*)[\s\S]*$/, '$1[redacted]'],
  // Postgres constraint details: Key (email)=(someone@example.com) already exists.
  [/(\bKey \([^)]*\)=\()[^)]*\)/g, '$1[redacted])'],
  [/(https?:\/\/[^\s?#'"]+)[?#][^\s'"]*/g, '$1?[redacted]'],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[email]'],
  // Credential-looking pairs; a value starting with "(" is Postgres wording, handled above.
  [/(bearer|token|key|secret|password)([=: ]+)(?!\()[^\s,;'"&]+/gi, '$1$2[redacted]'],
  // OAuth codes only in query form; "status code: 503" stays readable.
  [/(\bcode=)[^\s,;'"&]+/gi, '$1[redacted]'],
];

/** Redacts contact data and credentials from free text (error messages, breadcrumb messages). */
export function redactText(text: string): string {
  let out = text;
  for (const [pattern, replacement] of TEXT_RULES) out = out.replace(pattern, replacement);
  return out.length > MAX_TEXT ? `${out.slice(0, MAX_TEXT)}…` : out;
}

/** Drops the query string and fragment, which can carry tokens (email links, OAuth, SSE). */
export function stripQuery(url: string): string {
  const cut = url.search(/[?#]/);
  return cut === -1 ? url : url.slice(0, cut);
}

function scrubRecordUrls(record: Record<string, unknown>): void {
  for (const [key, value] of Object.entries(record)) {
    if (typeof value === 'string' && /url|path|from|^to$/i.test(key))
      record[key] = stripQuery(value);
  }
}

/** Returns null to drop the breadcrumb. */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  // Console output is free text we do not control.
  if (breadcrumb.category === 'console') return null;
  const out: Breadcrumb = { ...breadcrumb };
  if (out.message) out.message = redactText(out.message);
  if (out.data) {
    const data: Record<string, unknown> = { ...out.data };
    delete data['http.query'];
    delete data['http.fragment'];
    scrubRecordUrls(data);
    out.data = data;
  }
  return out;
}

export function scrubEvent<T extends ErrorEvent>(event: T): T {
  if (event.request) {
    const request = event.request;
    if (request.headers) {
      request.headers = Object.fromEntries(
        Object.entries(request.headers)
          .filter(([name]) => HEADER_ALLOWLIST.has(name.toLowerCase()))
          .map(([name, value]) => [
            name,
            name.toLowerCase() === 'referer' ? stripQuery(value) : value,
          ]),
      );
    }
    if (request.url) request.url = stripQuery(request.url);
    delete request.cookies;
    delete request.data;
    delete request.query_string;
    delete request.env;
  }
  for (const context of Object.values(event.contexts ?? {})) {
    if (context && typeof context === 'object') scrubRecordUrls(context);
  }
  for (const exception of event.exception?.values ?? []) {
    if (exception.value) exception.value = redactText(exception.value);
  }
  if (event.message) event.message = redactText(event.message);
  if (event.logentry?.message) event.logentry.message = redactText(event.logentry.message);
  if (event.transaction) event.transaction = stripQuery(event.transaction);
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs
      .map(scrubBreadcrumb)
      .filter((b): b is Breadcrumb => b !== null);
  }
  delete event.extra;
  if (event.user) event.user = event.user.id === undefined ? {} : { id: event.user.id };
  return event;
}

/**
 * Options every LeadForge Sentry client shares. Tracing is off unless a positive sample rate is
 * configured, and trace headers are never attached to outgoing requests (third-party sites and
 * Supabase must not receive our internal ids).
 */
export function baseSentryOptions(settings: {
  dsn: string;
  environment: string;
  tracesSampleRate?: number;
}) {
  const rate = settings.tracesSampleRate ?? 0;
  return {
    dsn: settings.dsn,
    environment: settings.environment,
    sendDefaultPii: false,
    ...(Number.isFinite(rate) && rate > 0 && rate <= 1 ? { tracesSampleRate: rate } : {}),
    tracePropagationTargets: [] as string[],
    beforeSend: <T extends ErrorEvent>(event: T): T => scrubEvent(event),
    beforeBreadcrumb: (breadcrumb: Breadcrumb): Breadcrumb | null => scrubBreadcrumb(breadcrumb),
  };
}
