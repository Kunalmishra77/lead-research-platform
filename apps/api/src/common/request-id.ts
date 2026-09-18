import type { IncomingMessage } from 'node:http';

import { uuidv7 } from 'uuidv7';

export const REQUEST_ID_HEADER = 'x-request-id';
const VALID_REQUEST_ID = /^[A-Za-z0-9._-]{8,128}$/;

/**
 * Fastify `genReqId`: reuse a well-formed incoming X-Request-Id, else a new UUID v7. The id is
 * written back to the raw headers so pino-http logs the same id Fastify exposes as `request.id`.
 */
export function generateRequestId(req: IncomingMessage): string {
  const incoming = req.headers[REQUEST_ID_HEADER];
  const id = typeof incoming === 'string' && VALID_REQUEST_ID.test(incoming) ? incoming : uuidv7();
  req.headers[REQUEST_ID_HEADER] = id;
  return id;
}
