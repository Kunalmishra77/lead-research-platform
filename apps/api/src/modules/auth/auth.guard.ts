import { type CanActivate, type ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { FastifyRequest } from 'fastify';

import { AppError } from '../../common/errors/app-error';
import { requestOrigin } from '../../common/request-origin';
import { SessionAuditService } from '../audit/session-audit.service';
import { IS_PUBLIC } from './public.decorator';
import { TokenVerifier } from './token-verifier';

const BEARER = /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/;

/** Global guard: every route requires a valid Supabase access token unless marked @Public(). */
@Injectable()
export class AuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly verifier: TokenVerifier,
    private readonly sessionAudit: SessionAuditService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean | undefined>(IS_PUBLIC, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic === true) return true;

    const request = context.switchToHttp().getRequest<FastifyRequest>();
    const match = BEARER.exec(request.headers.authorization ?? '');
    if (!match?.[1]) {
      throw new AppError({
        code: 'auth.missing_token',
        httpStatus: 401,
        title: 'Authentication required',
        detail: 'Send a Supabase access token as "Authorization: Bearer <token>"',
      });
    }
    request.auth = await this.verifier.verify(match[1]);
    // Login audit: first request of each Supabase session (detached; never delays or fails it).
    this.sessionAudit.track(request.auth, requestOrigin(request));
    return true;
  }
}
