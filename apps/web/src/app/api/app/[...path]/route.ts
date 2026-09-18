import type { NextRequest } from 'next/server';

import { env } from '@/env';
import { apiAuthHeaders } from '@/lib/api/server';
import {
  BodyTooLargeError,
  buildApiTarget,
  FORWARDED_REQUEST_HEADERS,
  isSameOrigin,
  pickResponseHeaders,
  problem,
  readCappedBody,
} from '@/lib/http/api-proxy';

/**
 * Same-origin proxy: browser -> Next.js -> API `/app/*`. The browser never holds the access token or
 * talks to the API/Supabase directly (ADR-0002); this adds the bearer token and workspace header
 * server-side. Responses are streamed through unchanged, which also covers SSE.
 */
export const dynamic = 'force-dynamic';

const MAX_BODY_BYTES = 1024 * 1024;

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  // CSRF (docs/10): session cookies are SameSite=Lax; additionally, state-changing requests must
  // come from our own origin.
  if (hasBody && !isSameOrigin(request.headers.get('origin'), env().APP_URL)) {
    return problem(403, 'forbidden', 'Cross-origin request refused');
  }
  const { path } = await context.params;
  const target = buildApiTarget(path, request.nextUrl.search, env().API_URL);
  if (!target) return problem(400, 'bad_request', 'Invalid path');

  let body: Uint8Array<ArrayBuffer> | undefined;
  if (hasBody) {
    if (Number(request.headers.get('content-length') ?? 0) > MAX_BODY_BYTES) {
      return problem(413, 'payload_too_large', 'Request body too large');
    }
    try {
      body = await readCappedBody(request.body, MAX_BODY_BYTES);
    } catch (err) {
      if (err instanceof BodyTooLargeError) {
        return problem(413, 'payload_too_large', 'Request body too large');
      }
      throw err;
    }
  }

  const headers = new Headers(await apiAuthHeaders());
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: 'no-store',
      redirect: 'manual', // never follow a redirect with the bearer token attached
      signal: request.signal, // client disconnect closes the upstream request (SSE)
    });
  } catch (err) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    console.error('api proxy upstream failure', {
      error_class: 'transient',
      path: target.pathname,
      message: err instanceof Error ? err.message : String(err),
    });
    return problem(502, 'upstream_unavailable', 'The API is not reachable. Try again shortly.');
  }
  if (upstream.status >= 300 && upstream.status < 400) {
    return problem(502, 'upstream_redirect', 'Unexpected redirect from the API');
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: pickResponseHeaders(upstream.headers),
  });
}

export { forward as DELETE, forward as GET, forward as PATCH, forward as POST };
