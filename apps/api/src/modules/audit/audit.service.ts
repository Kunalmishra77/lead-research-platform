import { auditLogs, sql, type Transaction, withUser } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { PinoLogger } from 'nestjs-pino';
import { uuidv7 } from 'uuidv7';

import { type Database, DB } from '../../infra/db/db.module';

/** Where a request came from; stored with every audit row (docs/10 audit logs). */
export interface RequestOrigin {
  ip: string | null;
  userAgent: string | null;
}

export interface TenantAuditEvent {
  orgId: string;
  actorUserId: string;
  /** Dotted, lowercase, e.g. `membership.role_changed` (DB check: ^[a-z][a-z0-9_.]{2,63}$). */
  action: string;
  targetType?: string;
  targetId?: string;
  meta?: Record<string, unknown>;
  origin?: RequestOrigin;
}

const MAX_USER_AGENT = 512;

@Injectable()
export class AuditService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(AuditService.name);
  }

  /**
   * Records a tenant event inside the caller's withTenant transaction, so the audit row commits or
   * rolls back together with the change it describes. RLS enforces org and actor.
   */
  async record(tx: Transaction, event: TenantAuditEvent): Promise<void> {
    await tx.insert(auditLogs).values({
      id: uuidv7(),
      orgId: event.orgId,
      actorUserId: event.actorUserId,
      action: event.action,
      targetType: event.targetType ?? null,
      targetId: event.targetId ?? null,
      meta: event.meta ?? {},
      ip: event.origin?.ip ?? null,
      userAgent: event.origin?.userAgent?.slice(0, MAX_USER_AGENT) ?? null,
    });
  }

  /** Org-less event for the calling user (only the `auth.` namespace), e.g. auth.session_started. */
  async recordPlatform(
    userId: string,
    action: `auth.${string}`,
    meta: Record<string, unknown>,
    origin: RequestOrigin,
  ): Promise<void> {
    await withUser(this.db, userId, (tx) =>
      tx.execute(sql`select app.record_platform_audit(
        ${uuidv7()}::uuid, ${action}, ${JSON.stringify(meta)}::jsonb,
        ${origin.ip}::inet, ${origin.userAgent?.slice(0, MAX_USER_AGENT) ?? null})`),
    );
  }
}
