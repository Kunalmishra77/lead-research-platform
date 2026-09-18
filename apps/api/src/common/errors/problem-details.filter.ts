import { type ArgumentsHost, Catch, type ExceptionFilter } from '@nestjs/common';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { PinoLogger } from 'nestjs-pino';

import { reportError, shouldReport } from '../observability/sentry';
import { toProblem } from './problem-details';

/** Global filter: every error leaves the API as `application/problem+json` (docs/05). */
@Catch()
export class ProblemDetailsFilter implements ExceptionFilter {
  constructor(private readonly logger: PinoLogger) {
    this.logger.setContext(ProblemDetailsFilter.name);
  }

  catch(exception: unknown, host: ArgumentsHost): void {
    const http = host.switchToHttp();
    const request = http.getRequest<FastifyRequest>();
    const reply = http.getResponse<FastifyReply>();
    const { body, errorClass, unexpected } = toProblem(exception, request.id);

    const context = { code: body.code, status: body.status, error_class: errorClass };
    if (unexpected) {
      this.logger.error({ ...context, err: exception }, 'request failed');
      if (shouldReport(unexpected, errorClass)) {
        reportError(exception, {
          requestId: request.id,
          code: body.code,
          userId: request.auth?.userId,
          orgId: request.tenant?.orgId,
        });
      }
    } else this.logger.info(context, 'request rejected');

    // Streaming/hijacked replies (SSE) may already have sent headers: log only, never send twice.
    if (reply.sent || reply.raw.headersSent) return;
    if (body.status === 401) {
      // RFC 6750: tell clients whether to refresh (invalid_token) or just authenticate.
      const tokenProblem =
        body.code === 'auth.invalid_token' || body.code === 'auth.session_revoked';
      void reply.header(
        'www-authenticate',
        tokenProblem ? 'Bearer error="invalid_token"' : 'Bearer',
      );
    }
    void reply.status(body.status).header('content-type', 'application/problem+json').send(body);
  }
}
