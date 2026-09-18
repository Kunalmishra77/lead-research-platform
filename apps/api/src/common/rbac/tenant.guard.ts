import { and, eq, memberships, sql, withUser } from '@leadforge/db';
import { type CanActivate, type ExecutionContext, Inject, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { FastifyRequest } from 'fastify';

import { type Database, DB } from '../../infra/db/db.module';
import { AppError } from '../errors/app-error';
import { hasPermission, type MembershipRole, type Permission } from './permissions';

export const REQUIRED_PERMISSION = 'rbac:permission';
export const WORKSPACE_HEADER = 'x-workspace-id';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Resolves the active workspace from X-Workspace-Id, loads the caller's membership (RLS: a user can
 * read their own memberships) and enforces the route's permission. Sets request.tenant.
 * Non-members and missing workspaces get the same 403, so ids cannot be probed.
 */
@Injectable()
export class TenantGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    @Inject(DB) private readonly db: Database,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const permission = this.reflector.getAllAndOverride<Permission | undefined>(
      REQUIRED_PERMISSION,
      [context.getHandler(), context.getClass()],
    );
    // Fail closed: a route using this guard without @RequirePermission is a programming error.
    if (permission === undefined) throw new Error('TenantGuard used without @RequirePermission()');
    const request = context.switchToHttp().getRequest<FastifyRequest>();
    const auth = request.auth;
    if (!auth) throw new Error('TenantGuard requires AuthGuard to run first');

    const header = request.headers[WORKSPACE_HEADER];
    const workspaceId = typeof header === 'string' ? header : '';
    if (!UUID.test(workspaceId)) {
      throw new AppError({
        code: 'tenant.workspace_required',
        httpStatus: 400,
        title: 'Workspace required',
        detail: `Send the active workspace id in the ${WORKSPACE_HEADER} header`,
        errorClass: 'invalid_input',
      });
    }

    const { active, membership } = await withUser(this.db, auth.userId, async (tx) => {
      // Tokens stay valid until expiry; reject signed-out/revoked sessions and banned/deleted users.
      const [session] = await tx.execute<{ active: boolean }>(
        sql`select app.session_is_active(${auth.sessionId}::uuid) as active`,
      );
      const [row] = await tx
        .select({ orgId: memberships.orgId, role: memberships.role })
        .from(memberships)
        .where(and(eq(memberships.workspaceId, workspaceId), eq(memberships.userId, auth.userId)))
        .limit(1);
      return { active: session?.active === true, membership: row };
    });
    if (!active) {
      throw new AppError({
        code: 'auth.session_revoked',
        httpStatus: 401,
        title: 'Session is no longer valid',
        detail: 'Sign in again',
      });
    }
    if (!membership) {
      throw new AppError({
        code: 'tenant.forbidden',
        httpStatus: 403,
        title: 'No access to this workspace',
      });
    }
    const role: MembershipRole = membership.role;
    if (!hasPermission(role, permission)) {
      throw new AppError({
        code: 'rbac.forbidden',
        httpStatus: 403,
        title: 'Your role does not allow this action',
        detail: `Requires permission ${permission}`,
      });
    }
    request.tenant = { orgId: membership.orgId, workspaceId, role };
    return true;
  }
}
