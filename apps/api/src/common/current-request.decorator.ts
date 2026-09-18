import { createParamDecorator, type ExecutionContext } from '@nestjs/common';
import type { FastifyRequest } from 'fastify';

import type { AuthUser, TenantInfo } from '../modules/auth/auth.types';

/** The verified caller. Only valid on non-@Public() routes. */
export const CurrentUser = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): AuthUser => {
    const auth = ctx.switchToHttp().getRequest<FastifyRequest>().auth;
    if (!auth) throw new Error('CurrentUser used on a route without AuthGuard');
    return auth;
  },
);

/** The active workspace context. Only valid on routes with @RequirePermission(). */
export const CurrentTenant = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): TenantInfo => {
    const tenant = ctx.switchToHttp().getRequest<FastifyRequest>().tenant;
    if (!tenant) throw new Error('CurrentTenant used on a route without @RequirePermission()');
    return tenant;
  },
);
