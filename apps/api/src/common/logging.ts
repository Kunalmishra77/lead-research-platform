import type { IncomingMessage } from 'node:http';

/** Header paths never written to logs (docs/10 logging hygiene). */
export const REDACTED_PATHS = [
  'req.headers.authorization',
  'req.headers["proxy-authorization"]',
  'req.headers.cookie',
  'req.headers["x-api-key"]',
  'res.headers["set-cookie"]',
];

export interface LoggedRequest {
  id: unknown;
  method: string | undefined;
  path: string;
  headers: IncomingMessage['headers'];
}

/**
 * pino `req` serializer: logs the path without its query string, because query parameters can
 * carry secrets (OAuth `code`, SSE tokens) that header redaction does not cover.
 */
export function serializeRequest(req: {
  id?: unknown;
  method?: string;
  url?: string;
  originalUrl?: string;
  headers: IncomingMessage['headers'];
}): LoggedRequest {
  const url = req.originalUrl ?? req.url ?? '';
  const queryAt = url.indexOf('?');
  return {
    id: req.id,
    method: req.method,
    path: queryAt === -1 ? url : url.slice(0, queryAt),
    headers: req.headers,
  };
}
