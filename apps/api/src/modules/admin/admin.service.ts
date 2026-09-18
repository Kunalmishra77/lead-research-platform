import { type SQL, sql, withUser } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { uuidv7 } from 'uuidv7';

import { assertSessionActive } from '../../common/db/session';
import { type Database, DB } from '../../infra/db/db.module';
import type { RequestOrigin } from '../audit/audit.service';
import type { AuthUser } from '../auth/auth.types';
import type { AdminOrg, AdminUser, Page } from './admin.dto';
import { mapAdminError, toAdminOrg, toAdminUser, toPage } from './admin.mapping';

/**
 * Cross-org reads for platform staff. The SECURITY DEFINER functions enforce the staff flag and
 * write the audit row (with request origin) in the same transaction (ADR-0003, migration 0011).
 */
@Injectable()
export class AdminService {
  constructor(@Inject(DB) private readonly db: Database) {}

  async listOrgs(
    user: AuthUser,
    origin: RequestOrigin,
    limit: number,
    cursor?: string,
  ): Promise<Page<AdminOrg>> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_list_orgs(${uuidv7()}::uuid, ${limit + 1}::int,
        ${cursor ?? null}::uuid, ${origin.ip}::inet, ${origin.userAgent})`,
    );
    // Parsed after commit: on a row-shape mismatch the access is still audited, then 500s.
    return toPage(limit, rows, toAdminOrg);
  }

  async listUsers(
    user: AuthUser,
    origin: RequestOrigin,
    limit: number,
    cursor?: string,
  ): Promise<Page<AdminUser>> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_list_users(${uuidv7()}::uuid, ${limit + 1}::int,
        ${cursor ?? null}::uuid, ${origin.ip}::inet, ${origin.userAgent})`,
    );
    return toPage(limit, rows, toAdminUser);
  }

  private async call(user: AuthUser, query: SQL): Promise<unknown[]> {
    try {
      return await withUser(this.db, user.userId, async (tx) => {
        await assertSessionActive(tx, user.sessionId);
        return [...(await tx.execute(query))];
      });
    } catch (err) {
      throw mapAdminError(err);
    }
  }
}
