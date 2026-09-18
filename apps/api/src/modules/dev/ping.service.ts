import type { JobEnvelope } from '@leadforge/contracts';
import { and, eq, jobRuns, withTenant } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { uuidv7 } from 'uuidv7';

import { AppError } from '../../common/errors/app-error';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import { type Database, DB } from '../../infra/db/db.module';
import { assertValidEnvelope, JobPublisher } from '../../infra/streams/job-publisher';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import type { JobRunView } from './ping.dto';

export const PING_JOB_TYPE = 'system.ping';

export interface PingInput {
  message: string;
  delayMs: number;
  steps: number;
  failTimes: number;
}

const notFound = () =>
  new AppError({ code: 'job.not_found', httpStatus: 404, title: 'Job not found' });

@Injectable()
export class PingService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly publisher: JobPublisher,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  /**
   * Validates the envelope, creates the job_runs row, then publishes (docs/05: 202 + job id).
   * If publishing fails the row is marked failed, so no `queued` row is left behind.
   */
  async create(
    user: AuthUser,
    tenant: TenantInfo,
    input: PingInput,
  ): Promise<{ jobId: string; status: 'queued' }> {
    const jobId = uuidv7();
    const ctx = { orgId: tenant.orgId, userId: user.userId };
    const payload = {
      message: input.message,
      delay_ms: input.delayMs,
      steps: input.steps,
      fail_times: input.failTimes,
    };
    const envelope: JobEnvelope = {
      envelope_version: 1,
      job_id: jobId,
      type: PING_JOB_TYPE,
      org_id: tenant.orgId,
      research_job_id: null,
      idempotency_key: `${PING_JOB_TYPE}:${jobId}`,
      attempt: 1,
      priority: 'interactive',
      budget: { credits_remaining: 0, cost_cap_micros: 0 },
      // Server-generated (never client input), 32 hex chars.
      trace_id: uuidv7().replaceAll('-', ''),
      payload,
      created_at: new Date().toISOString(),
    };
    assertValidEnvelope(envelope);
    await withTenant(this.db, ctx, (tx) =>
      tx.insert(jobRuns).values({
        id: jobId,
        orgId: tenant.orgId,
        workspaceId: tenant.workspaceId,
        type: PING_JOB_TYPE,
        input: payload,
        createdBy: user.userId,
      }),
    );
    try {
      await this.publisher.publish(this.config.JOBS_SYSTEM_POOL, envelope);
    } catch (err) {
      await withTenant(this.db, ctx, (tx) =>
        tx
          .update(jobRuns)
          .set({
            status: 'failed',
            errorClass: 'transient',
            error: 'queue unavailable',
            finishedAt: new Date(),
          })
          .where(eq(jobRuns.id, jobId)),
      );
      throw err;
    }
    return { jobId, status: 'queued' };
  }

  async get(user: AuthUser, tenant: TenantInfo, jobId: string): Promise<JobRunView> {
    const [row] = await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        .select()
        .from(jobRuns)
        .where(and(eq(jobRuns.id, jobId), eq(jobRuns.workspaceId, tenant.workspaceId)))
        .limit(1),
    );
    if (!row) throw notFound();
    return {
      id: row.id,
      type: row.type,
      status: row.status,
      attempts: row.attempts,
      result: row.result,
      errorClass: row.errorClass,
      error: row.error,
      createdAt: row.createdAt.toISOString(),
      finishedAt: row.finishedAt?.toISOString() ?? null,
    };
  }
}
