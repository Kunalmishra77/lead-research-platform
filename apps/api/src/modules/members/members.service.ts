import { and, eq, memberships, sql, withTenant } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';

import { AppError } from '../../common/errors/app-error';
import type { MembershipRole } from '../../common/rbac/permissions';
import { type Database, DB } from '../../infra/db/db.module';
import { AuditService, type RequestOrigin } from '../audit/audit.service';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import type { Member } from './members.dto';

@Injectable()
export class MembersService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly audit: AuditService,
  ) {}

  async list(user: AuthUser, tenant: TenantInfo): Promise<Member[]> {
    const rows = await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx.execute<{
        user_id: string;
        email: string | null;
        full_name: string | null;
        role: string;
        joined_at: string;
      }>(sql`select user_id::text, email, full_name, role::text, joined_at::text
             from app.workspace_members(${tenant.workspaceId}::uuid)`),
    );
    return rows.map((r) => ({
      userId: r.user_id,
      email: r.email,
      fullName: r.full_name,
      role: r.role,
      joinedAt: r.joined_at,
    }));
  }

  /**
   * Changes a member's role in the active workspace (audited in the same transaction).
   * Only owners may grant or revoke `owner`; the last owner cannot be demoted.
   */
  async changeRole(
    user: AuthUser,
    tenant: TenantInfo,
    targetUserId: string,
    newRole: MembershipRole,
    origin: RequestOrigin,
  ): Promise<{ userId: string; role: MembershipRole }> {
    return withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, async (tx) => {
      const inWorkspace = and(
        eq(memberships.workspaceId, tenant.workspaceId),
        eq(memberships.orgId, tenant.orgId),
      );
      // Lock every membership of the workspace: role checks below see committed, current roles
      // (a concurrent demotion of the actor or of another owner is serialized behind this lock).
      const members = await tx
        .select({ userId: memberships.userId, role: memberships.role })
        .from(memberships)
        .where(inWorkspace)
        .for('update');
      const target = members.find((m) => m.userId === targetUserId);
      if (!target) {
        throw new AppError({
          code: 'member.not_found',
          httpStatus: 404,
          title: 'Member not found',
        });
      }
      const oldRole: MembershipRole = target.role;
      if (oldRole === newRole) return { userId: targetUserId, role: newRole };

      // The actor's role as of now (the guard's role may be stale by the time the lock is held).
      const actorRole = members.find((m) => m.userId === user.userId)?.role;
      if (actorRole !== 'owner' && actorRole !== 'admin') {
        throw new AppError({
          code: 'rbac.forbidden',
          httpStatus: 403,
          title: 'Your role does not allow this action',
        });
      }
      if ((newRole === 'owner' || oldRole === 'owner') && actorRole !== 'owner') {
        throw new AppError({
          code: 'member.owner_only',
          httpStatus: 403,
          title: 'Only an owner can grant or remove the owner role',
        });
      }
      const ownerCount = members.filter((m) => m.role === 'owner').length;
      if (oldRole === 'owner' && ownerCount <= 1) {
        throw new AppError({
          code: 'member.last_owner',
          httpStatus: 409,
          title: 'A workspace needs at least one owner',
          errorClass: 'invalid_input',
        });
      }

      await tx
        .update(memberships)
        .set({ role: newRole })
        .where(and(inWorkspace, eq(memberships.userId, targetUserId)));
      await this.audit.record(tx, {
        orgId: tenant.orgId,
        actorUserId: user.userId,
        action: 'membership.role_changed',
        targetType: 'user',
        targetId: targetUserId,
        meta: { workspace_id: tenant.workspaceId, from: oldRole, to: newRole },
        origin,
      });
      return { userId: targetUserId, role: newRole };
    });
  }
}
