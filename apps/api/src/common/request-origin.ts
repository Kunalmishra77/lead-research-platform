import type { FastifyRequest } from 'fastify';

import type { RequestOrigin } from '../modules/audit/audit.service';

/** Client address and user agent for audit rows. Fastify's request.ip honours trustProxy only. */
export function requestOrigin(request: FastifyRequest): RequestOrigin {
  const ua = request.headers['user-agent'];
  return { ip: request.ip || null, userAgent: typeof ua === 'string' ? ua : null };
}
