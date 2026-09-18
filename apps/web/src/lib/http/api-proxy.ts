/** Pure helpers for the same-origin API proxy (`app/api/app/[...path]/route.ts`). */

const SEGMENT = /^[A-Za-z0-9._~-]+$/;

/** Only these upstream headers reach the browser (never set-cookie or hop-by-hop headers). */
export const FORWARDED_RESPONSE_HEADERS = [
  'content-type',
  'cache-control',
  'retry-after',
  'x-request-id',
  'www-authenticate',
] as const;

/** Only these browser headers reach the API (never cookies, host or authorization). */
export const FORWARDED_REQUEST_HEADERS = ['content-type', 'accept', 'last-event-id'] as const;

/**
 * Builds the upstream URL under `/app/`, or null if the path could escape that prefix
 * (`.`/`..` segments, including percent-encoded ones, which arrive decoded).
 */
export function buildApiTarget(
  path: readonly string[],
  search: string,
  apiUrl: string,
): URL | null {
  if (path.length === 0) return null;
  if (!path.every((s) => SEGMENT.test(s) && s !== '.' && s !== '..')) return null;
  const target = new URL(`/app/${path.join('/')}`, apiUrl);
  if (!target.pathname.startsWith('/app/')) return null;
  target.search = search;
  return target;
}

/** CSRF check for state-changing requests: the Origin header must be exactly our own origin. */
export function isSameOrigin(originHeader: string | null, appUrl: string): boolean {
  return originHeader !== null && originHeader === new URL(appUrl).origin;
}

export function pickResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.get(name);
    if (value) out.set(name, value);
  }
  if (upstream.get('content-type')?.startsWith('text/event-stream')) {
    // no-transform keeps compression middleware from buffering the stream.
    out.set('cache-control', 'no-cache, no-transform');
    out.set('x-accel-buffering', 'no');
  }
  return out;
}

export class BodyTooLargeError extends Error {}

/** Reads a request body, failing as soon as it exceeds `maxBytes` (no unbounded buffering). */
export async function readCappedBody(
  body: ReadableStream<Uint8Array> | null,
  maxBytes: number,
): Promise<Uint8Array<ArrayBuffer> | undefined> {
  if (!body) return undefined;
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new BodyTooLargeError();
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

export function problem(status: number, code: string, title: string): Response {
  return Response.json(
    { type: 'about:blank', status, code, title },
    { status, headers: { 'content-type': 'application/problem+json' } },
  );
}
