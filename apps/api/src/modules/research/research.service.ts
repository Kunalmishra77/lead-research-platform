import type { JobEnvelope, ResearchSpec } from '@leadforge/contracts';
import {
  and,
  companies,
  companyLocations,
  desc,
  eq,
  fieldValues,
  inArray,
  industries,
  leads,
  lt,
  researchJobs,
  searches,
  sources,
  sql,
  withTenant,
} from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';
import { PinoLogger } from 'nestjs-pino';
import { uuidv7 } from 'uuidv7';

import { isRetryable } from '../../common/db/pg-error';
import { AppError } from '../../common/errors/app-error';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import { type Database, DB } from '../../infra/db/db.module';
import { REDIS } from '../../infra/redis/redis.module';
import { JobPublisher } from '../../infra/streams/job-publisher';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import { CreditsService } from '../credits/credits.service';
import type {
  LeadsPage,
  LeadValue,
  LeadView,
  ResearchJobListItem,
  ResearchJobView,
  ResearchPage,
} from './research.dto';

/** The planner picks up this job type (task 2.10); discovery tasks fan out from there. */
export const RESEARCH_PLAN_JOB_TYPE = 'research.plan';

/** Statuses a user can still cancel (docs/05 research endpoints). */
const CANCELLABLE = ['queued', 'planning', 'running', 'paused'] as const;
const TERMINAL: readonly string[] = ['completed', 'failed', 'cancelled'];
const isCancellable = (status: string): boolean =>
  (CANCELLABLE as readonly string[]).includes(status);

/**
 * A cancelled job must stop spending even though its envelope is already queued. The planner and
 * executor (tasks 2.10/2.11) check this key before each step; it outlives the longest run.
 */
export const cancelKey = (jobId: string): string => `research:cancelled:${jobId}`;
const CANCEL_FLAG_TTL_SECONDS = 24 * 60 * 60;

const notFound = () =>
  new AppError({ code: 'research.not_found', httpStatus: 404, title: 'Research job not found' });

@Injectable()
export class ResearchService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly credits: CreditsService,
    private readonly publisher: JobPublisher,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    @Inject(REDIS) private readonly redis: Redis,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(ResearchService.name);
  }

  /**
   * Creates the search + job and reserves credits in one transaction (docs/05: 202 + job id), then
   * publishes the envelope. Nothing is left behind: a failed reservation rolls the rows back, and a
   * failed publish marks the job failed and returns the credits.
   */
  async create(
    user: AuthUser,
    tenant: TenantInfo,
    input: { rawQuery: string; spec: ResearchSpec; traceId: string },
  ): Promise<{ jobId: string; status: 'queued'; credits: { reserved: number; balance: number } }> {
    const ctx = { orgId: tenant.orgId, userId: user.userId };
    await this.assertTaxonomyExists(ctx, input.spec);
    const estimate = await this.credits.estimate(tenant.orgId, user.userId, {
      depth: input.spec.depth,
      maxResults: input.spec.limits.max_results,
      maxCredits: input.spec.limits.max_credits,
    });

    const searchId = uuidv7();
    const jobId = uuidv7();
    const balance = await withTenant(this.db, ctx, async (tx) => {
      // Take the org's credit lock BEFORE inserting: the inserts hold a KEY SHARE lock on the
      // organization row (foreign keys), and app.credit_reserve upgrades it to FOR UPDATE, so two
      // concurrent creates in one org would deadlock. An advisory lock lets them queue instead.
      await tx.execute(
        sql`select pg_advisory_xact_lock(hashtextextended('credits:' || ${tenant.orgId}::text, 0))`,
      );
      await tx.insert(searches).values({
        id: searchId,
        orgId: tenant.orgId,
        workspaceId: tenant.workspaceId,
        userId: user.userId,
        rawQuery: input.rawQuery,
        spec: input.spec,
        specVersion: input.spec.spec_version,
      });
      await tx.insert(researchJobs).values({
        id: jobId,
        orgId: tenant.orgId,
        workspaceId: tenant.workspaceId,
        searchId,
        createdBy: user.userId,
        depth: input.spec.depth,
      });
      // Same transaction: if the balance is short (402) the search and the job never existed.
      return this.credits.reserveIn(tx, jobId, estimate.reserve).catch((err: unknown) => {
        throw this.credits.toCreditError(err, estimate.reserve);
      });
    }).catch((err: unknown) => {
      // Lock waits can still end in a serialization failure; that is worth retrying, not a 500.
      throw isRetryable(err)
        ? new AppError({
            code: 'research.busy',
            httpStatus: 503,
            title: 'Too many runs starting at once',
            detail: 'Try again in a moment.',
            errorClass: 'transient',
            cause: err,
          })
        : err;
    });

    const envelope: JobEnvelope = {
      envelope_version: 1,
      job_id: jobId,
      type: RESEARCH_PLAN_JOB_TYPE,
      org_id: tenant.orgId,
      research_job_id: jobId,
      idempotency_key: `${RESEARCH_PLAN_JOB_TYPE}:${jobId}`,
      attempt: 1,
      priority: 'interactive',
      budget: {
        credits_remaining: estimate.reserve,
        cost_cap_micros: estimate.reserve * this.config.COST_CAP_MICROS_PER_CREDIT,
      },
      // Same id as the API request log, so a run can be followed across services.
      trace_id: input.traceId,
      payload: { search_id: searchId, spec: input.spec },
      created_at: new Date().toISOString(),
    };
    try {
      await this.publisher.publish(this.config.JOBS_DISCOVERY_POOL, envelope);
    } catch (err) {
      // Compensate the dual write: the row exists but nothing will run it.
      try {
        await this.failAndSettle(user, tenant, jobId);
      } catch (compensation: unknown) {
        this.logger.error(
          { org_id: tenant.orgId, job_id: jobId, trace_id: input.traceId, err: compensation },
          'research job left reserved after a failed publish',
        );
      }
      throw err;
    }
    this.logger.info(
      {
        org_id: tenant.orgId,
        job_id: jobId,
        trace_id: input.traceId,
        depth: input.spec.depth,
        credits_reserved: estimate.reserve,
      },
      'research job queued',
    );
    return { jobId, status: 'queued', credits: { reserved: estimate.reserve, balance } };
  }

  async get(user: AuthUser, tenant: TenantInfo, jobId: string): Promise<ResearchJobView> {
    const ctx = { orgId: tenant.orgId, userId: user.userId };
    const [row] = await withTenant(this.db, ctx, (tx) =>
      tx
        .select({
          id: researchJobs.id,
          status: researchJobs.status,
          depth: researchJobs.depth,
          progress: researchJobs.progress,
          creditsReserved: researchJobs.creditsReserved,
          creditsUsed: researchJobs.creditsUsed,
          errorClass: researchJobs.errorClass,
          createdAt: researchJobs.createdAt,
          startedAt: researchJobs.startedAt,
          finishedAt: researchJobs.finishedAt,
          rawQuery: searches.rawQuery,
          spec: searches.spec,
        })
        .from(researchJobs)
        .leftJoin(searches, eq(searches.id, researchJobs.searchId))
        .where(and(eq(researchJobs.id, jobId), eq(researchJobs.workspaceId, tenant.workspaceId)))
        .limit(1),
    );
    if (!row) throw notFound();
    return {
      id: row.id,
      status: row.status,
      depth: row.depth,
      rawQuery: row.rawQuery ?? '',
      spec: row.spec as ResearchSpec,
      progress: row.progress,
      credits: {
        reserved: row.creditsReserved,
        used: row.creditsUsed,
        balance: await this.credits.balance(tenant.orgId, user.userId),
      },
      errorClass: row.errorClass,
      createdAt: row.createdAt.toISOString(),
      startedAt: row.startedAt?.toISOString() ?? null,
      finishedAt: row.finishedAt?.toISOString() ?? null,
    };
  }

  /** History for the active workspace, newest first (docs/05 `GET /app/research`). */
  /**
   * The leads this job delivered to the active workspace, newest first.
   *
   * Reads `leads` rather than the graph: `companies` is shared, and being in it says nothing
   * about who is entitled to see it (ADR-0012). The join is what scopes this to one workspace,
   * and RLS is what stops a wrong `research_job_id` reaching another tenant's rows.
   */
  async results(
    user: AuthUser,
    tenant: TenantInfo,
    jobId: string,
    query: { limit: number; cursor?: string },
  ): Promise<LeadsPage> {
    // 404 before anything else, so an id from another workspace cannot be probed by the shape
    // of the answer.
    await this.get(user, tenant, jobId);

    const rows = await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        // One row per lead, not per location. A chain resolved to one company keeps a location
        // for each branch (graph.py _resolve_company), so a plain join returns that company once
        // per branch: the page repeats the same lead, and `limit` counts rows rather than leads,
        // so a page can end mid-company and the cursor skips the rest of it.
        .selectDistinctOn([leads.id], {
          id: leads.id,
          companyId: leads.companyId,
          status: leads.status,
          createdAt: leads.createdAt,
          name: companies.canonicalName,
          domain: companies.primaryDomain,
          city: companies.city,
          country: companies.country,
          address: companyLocations.address,
          phone: companyLocations.phoneE164,
          googlePlaceId: companyLocations.googlePlaceId,
        })
        .from(leads)
        .innerJoin(companies, eq(companies.id, leads.companyId))
        .leftJoin(companyLocations, eq(companyLocations.companyId, companies.id))
        .where(
          and(
            eq(leads.researchJobId, jobId),
            eq(leads.workspaceId, tenant.workspaceId),
            // UUID v7 ids sort by creation time, so the id alone is a stable cursor.
            query.cursor ? lt(leads.id, query.cursor) : undefined,
          ),
        )
        // DISTINCT ON keeps the first row per lead in this order, so the tie-break decides which
        // branch is shown: the oldest location, which is the one the job saw first.
        .orderBy(desc(leads.id), companyLocations.id)
        .limit(query.limit + 1),
    );

    const page = rows.slice(0, query.limit);
    const nextCursor = rows.length > query.limit ? (page.at(-1)?.id ?? null) : null;
    const byCompany = await this.currentValues(
      user,
      tenant,
      page.map((row) => row.companyId),
    );

    const items: LeadView[] = page.map((row) => ({
      id: row.id,
      companyId: row.companyId,
      status: row.status,
      createdAt: row.createdAt.toISOString(),
      name: row.name,
      domain: row.domain,
      city: row.city,
      country: row.country,
      address: row.address,
      phone: row.phone,
      googlePlaceId: row.googlePlaceId,
      values: byCompany.get(row.companyId) ?? [],
    }));

    return {
      items,
      nextCursor,
      sources: [...new Set(items.flatMap((item) => item.values.map((v) => v.source)))].sort(),
    };
  }

  /**
   * The current value of every field of these companies, with where each came from.
   *
   * One query for the page rather than one per row: a page of 50 leads with fifteen fields each
   * is 750 values, and asking for them separately is how a results grid becomes slow enough to
   * be unusable. `is_current` is the filter — `field_values` is append-only, so without it every
   * value a company has ever had comes back.
   */
  private async currentValues(
    user: AuthUser,
    tenant: TenantInfo,
    companyIds: string[],
  ): Promise<Map<string, LeadValue[]>> {
    const byCompany = new Map<string, LeadValue[]>();
    if (companyIds.length === 0) return byCompany;

    const rows = await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        .select({
          entityId: fieldValues.entityId,
          field: fieldValues.field,
          value: fieldValues.value,
          source: sources.key,
          sourceUrl: fieldValues.sourceUrl,
          observedAt: fieldValues.observedAt,
          method: fieldValues.method,
          derivation: fieldValues.derivation,
          confidence: fieldValues.confidence,
        })
        .from(fieldValues)
        .innerJoin(sources, eq(sources.id, fieldValues.sourceId))
        .where(
          and(
            eq(fieldValues.entityType, 'company'),
            inArray(fieldValues.entityId, companyIds),
            eq(fieldValues.isCurrent, true),
          ),
        )
        .orderBy(fieldValues.field),
    );

    for (const row of rows) {
      const list = byCompany.get(row.entityId) ?? [];
      list.push({
        field: row.field,
        value: row.value,
        source: row.source,
        sourceUrl: row.sourceUrl,
        observedAt: row.observedAt.toISOString(),
        method: row.method,
        derivation: row.derivation,
        confidence: row.confidence,
      });
      byCompany.set(row.entityId, list);
    }
    return byCompany;
  }

  async list(
    user: AuthUser,
    tenant: TenantInfo,
    query: { limit: number; cursor?: string },
  ): Promise<ResearchPage> {
    const rows = await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        .select({
          id: researchJobs.id,
          status: researchJobs.status,
          depth: researchJobs.depth,
          creditsReserved: researchJobs.creditsReserved,
          creditsUsed: researchJobs.creditsUsed,
          createdAt: researchJobs.createdAt,
          finishedAt: researchJobs.finishedAt,
          rawQuery: searches.rawQuery,
        })
        .from(researchJobs)
        .leftJoin(searches, eq(searches.id, researchJobs.searchId))
        .where(
          and(
            eq(researchJobs.workspaceId, tenant.workspaceId),
            // UUID v7 ids sort by creation time, so the id alone is a stable cursor.
            query.cursor ? lt(researchJobs.id, query.cursor) : undefined,
          ),
        )
        .orderBy(desc(researchJobs.id))
        .limit(query.limit + 1),
    );
    const items: ResearchJobListItem[] = rows.slice(0, query.limit).map((r) => ({
      id: r.id,
      status: r.status,
      depth: r.depth,
      rawQuery: r.rawQuery ?? '',
      creditsReserved: r.creditsReserved,
      creditsUsed: r.creditsUsed,
      createdAt: r.createdAt.toISOString(),
      finishedAt: r.finishedAt?.toISOString() ?? null,
    }));
    const last = items.at(-1);
    return { items, nextCursor: rows.length > query.limit && last ? last.id : null };
  }

  /** Stops a running job and returns the unused credits (docs/05 cancel). Idempotent. */
  async cancel(user: AuthUser, tenant: TenantInfo, jobId: string): Promise<ResearchJobView> {
    const current = await this.get(user, tenant, jobId);
    if (TERMINAL.includes(current.status)) return current;
    if (!isCancellable(current.status)) {
      throw new AppError({
        code: 'research.not_cancellable',
        httpStatus: 409,
        title: 'This job can no longer be cancelled',
        detail: `Status is ${current.status}`,
      });
    }
    // Raise the flag first: a worker that is mid-run stops at its next check, so it cannot
    // deliver (and bill) more after the reservation has been released.
    await this.redis.set(cancelKey(jobId), '1', 'EX', CANCEL_FLAG_TTL_SECONDS);
    await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        .update(researchJobs)
        .set({ status: 'cancelled', finishedAt: new Date() })
        .where(
          and(
            eq(researchJobs.id, jobId),
            eq(researchJobs.workspaceId, tenant.workspaceId),
            inArray(researchJobs.status, CANCELLABLE),
          ),
        ),
    );
    const settled = await this.credits.settle(tenant.orgId, user.userId, jobId);
    this.logger.info({ org_id: tenant.orgId, job_id: jobId, ...settled }, 'research job cancelled');
    return this.get(user, tenant, jobId);
  }

  private async failAndSettle(user: AuthUser, tenant: TenantInfo, jobId: string): Promise<void> {
    await withTenant(this.db, { orgId: tenant.orgId, userId: user.userId }, (tx) =>
      tx
        .update(researchJobs)
        .set({ status: 'failed', errorClass: 'transient', finishedAt: new Date() })
        .where(and(eq(researchJobs.id, jobId), eq(researchJobs.workspaceId, tenant.workspaceId))),
    );
    await this.credits.settle(tenant.orgId, user.userId, jobId);
  }

  /** Taxonomy ids are only pattern-checked by the schema; they must also exist (task 2.1). */
  private async assertTaxonomyExists(
    ctx: { orgId: string; userId: string },
    spec: ResearchSpec,
  ): Promise<void> {
    const wanted = spec.filters.industry?.taxonomy_ids ?? [];
    if (wanted.length === 0) return;
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx
        .select({ slug: industries.slug })
        .from(industries)
        .where(inArray(industries.slug, [...wanted])),
    );
    const known = new Set(rows.map((r) => r.slug));
    const unknown = wanted.filter((slug) => !known.has(slug));
    if (unknown.length > 0) {
      throw new AppError({
        code: 'research.unknown_industry',
        httpStatus: 422,
        title: 'Unknown industry',
        detail: `No such industry: ${unknown.join(', ')}`,
        errorClass: 'invalid_input',
      });
    }
  }
}
