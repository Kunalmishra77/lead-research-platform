import { type SQL, sql, withUser } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { uuidv7 } from 'uuidv7';

import { assertSessionActive } from '../../common/db/session';
import { type Database, DB } from '../../infra/db/db.module';
import type { RequestOrigin } from '../audit/audit.service';
import type { AuthUser } from '../auth/auth.types';
import type {
  AdminConnector,
  AdminJob,
  AdminMeter,
  AdminOrg,
  AdminTask,
  AdminUser,
  Page,
} from './admin.dto';
import {
  mapAdminError,
  toAdminConnector,
  toAdminJob,
  toAdminMeter,
  toAdminOrg,
  toAdminTask,
  toAdminUser,
  toPage,
} from './admin.mapping';

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

  /**
   * Research jobs across every org, newest first. The page exists to answer "why did that job
   * find nothing?" without reading container logs, which is how every live failure so far had to
   * be diagnosed.
   */
  async listJobs(
    user: AuthUser,
    origin: RequestOrigin,
    limit: number,
    cursor?: string,
    status?: AdminJob['status'],
  ): Promise<Page<AdminJob>> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_list_jobs(${uuidv7()}::uuid, ${limit + 1}::int,
        ${cursor ?? null}::uuid, ${status ?? null}::app.research_job_status,
        ${origin.ip}::inet, ${origin.userAgent})`,
    );
    return toPage(limit, rows, toAdminJob);
  }

  /** One job's tasks with their parent links, so the caller can draw the DAG rather than a list. */
  async jobTasks(user: AuthUser, origin: RequestOrigin, jobId: string): Promise<AdminTask[]> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_job_tasks(${uuidv7()}::uuid, ${jobId}::uuid,
        ${origin.ip}::inet, ${origin.userAgent})`,
    );
    return rows.map(toAdminTask);
  }

  async connectorHealth(
    user: AuthUser,
    origin: RequestOrigin,
    since?: string,
  ): Promise<AdminConnector[]> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_connector_health(${uuidv7()}::uuid, ${since ?? null}::timestamptz,
        ${origin.ip}::inet, ${origin.userAgent})`,
    );
    return rows.map(toAdminConnector);
  }

  /** What was billed, by meter. The only honest cost figure: see migration 0022. */
  async usageByMeter(user: AuthUser, origin: RequestOrigin, since?: string): Promise<AdminMeter[]> {
    const rows = await this.call(
      user,
      sql`select * from app.admin_usage_by_meter(${uuidv7()}::uuid, ${since ?? null}::timestamptz,
        ${origin.ip}::inet, ${origin.userAgent})`,
    );
    return rows.map(toAdminMeter);
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
