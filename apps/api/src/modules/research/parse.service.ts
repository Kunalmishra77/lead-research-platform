import {
  type JobEnvelope,
  type ParseFeasibility,
  type ResearchSpec,
  validateResearchParseReply,
  validateResearchSpec,
} from '@leadforge/contracts';
import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';
import { PinoLogger } from 'nestjs-pino';

import { AppError } from '../../common/errors/app-error';
import { APP_CONFIG } from '../../config/config.module';
import type { AppConfig } from '../../config/env.schema';
import { REDIS } from '../../infra/redis/redis.module';
import { groupFor, JobPublisher, streamFor } from '../../infra/streams/job-publisher';
import { JobRpc, replyKeyFor } from '../../infra/streams/job-rpc';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import { type CreditEstimate, CreditsService } from '../credits/credits.service';

export const RESEARCH_PARSE_JOB_TYPE = 'research.parse';

/** Longest sentence we will parse; the worker's payload schema agrees. */
export const MAX_QUERY_LENGTH = 2000;

const rateKey = (orgId: string, minute: number): string => `parse:rate:${orgId}:${minute}`;

/** How an error from the worker becomes an HTTP status the caller can act on. */
const STATUS_BY_ERROR_CLASS: Record<string, number> = {
  invalid_input: 400,
  parse_failed: 422,
  rate_limited: 429,
  budget_exhausted: 402,
  access_restricted: 403,
  transient: 503,
};

export interface ParseInput {
  rawQuery: string;
  traceId: string;
}

export interface ParseResultView {
  spec: ResearchSpec;
  feasibility: ParseFeasibility[];
  needsConfirmation: boolean;
  ambiguities: string[];
  unsupported: string[];
  confidence: number;
  estimate: CreditEstimate;
  provenance: { model: string; promptVersion: string; observedAt: string; cached: boolean };
}

/**
 * Turns a sentence into a reviewable ResearchSpec (ADR-0005).
 *
 * The parse itself runs in a worker, because that is where the prompts, the eval set and the
 * metered gateway live. The credit estimate is added here, by the same service the create
 * endpoint reserves against, so the number shown is the number that will be charged.
 */
@Injectable()
export class ParseService {
  constructor(
    private readonly publisher: JobPublisher,
    private readonly rpc: JobRpc,
    private readonly credits: CreditsService,
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    private readonly logger: PinoLogger,
  ) {
    this.logger.setContext(ParseService.name);
  }

  async parse(tenant: TenantInfo, user: AuthUser, input: ParseInput): Promise<ParseResultView> {
    await this.checkRate(tenant.orgId);
    await this.assertWorkerAvailable();

    const jobId = this.rpc.newJobId();
    const replyTo = replyKeyFor(jobId);
    const envelope: JobEnvelope = {
      envelope_version: 1,
      job_id: jobId,
      type: RESEARCH_PARSE_JOB_TYPE,
      org_id: tenant.orgId,
      // No job exists yet: the user is deciding whether to start one. The model call is still
      // metered, against the org (docs/11).
      research_job_id: null,
      idempotency_key: `${RESEARCH_PARSE_JOB_TYPE}:${jobId}`,
      attempt: 1,
      priority: 'interactive',
      budget: { credits_remaining: 0, cost_cap_micros: this.config.PARSE_COST_CAP_MICROS },
      trace_id: input.traceId,
      payload: { raw_query: input.rawQuery, reply_to: replyTo },
      created_at: new Date().toISOString(),
    };

    await this.publisher.publish(this.config.JOBS_INTERACTIVE_POOL, envelope);
    const raw = await this.rpc.await(replyTo, this.config.PARSE_TIMEOUT_MS);
    if (raw === null) {
      this.logger.error(
        { jobId, traceId: input.traceId, pool: this.config.JOBS_INTERACTIVE_POOL },
        'no worker answered a parse request',
      );
      throw new AppError({
        code: 'research.parse_unavailable',
        httpStatus: 503,
        title: 'Parsing is temporarily unavailable',
        detail: 'No worker answered in time. Nothing was charged; try again in a moment.',
        errorClass: 'transient',
      });
    }

    const reply = validateResearchParseReply(raw);
    if (!reply.ok) {
      throw new AppError({
        code: 'research.parse_invalid_reply',
        httpStatus: 502,
        title: 'Parsing returned an unusable answer',
        detail: reply.errors.join('; '),
        errorClass: 'parse_failed',
      });
    }
    if (!reply.value.ok || !reply.value.result) {
      const error = reply.value.error;
      throw new AppError({
        code: `research.parse_${error?.error_class ?? 'failed'}`,
        httpStatus: STATUS_BY_ERROR_CLASS[error?.error_class ?? 'transient'] ?? 503,
        title: 'Could not read that request',
        detail: error?.message,
        errorClass: error?.error_class ?? 'transient',
      });
    }

    const result = reply.value.result;
    // The reply contract only says `spec` is an object; the spec has its own contract, and
    // reading limits off an unchecked one would be a 500 rather than an honest 502.
    const checked = validateResearchSpec(result.spec);
    if (!checked.ok) {
      throw new AppError({
        code: 'research.parse_invalid_reply',
        httpStatus: 502,
        title: 'Parsing returned an unusable spec',
        detail: checked.errors.join('; '),
        errorClass: 'parse_failed',
      });
    }
    const spec = checked.value;
    const estimate = await this.credits.estimate(tenant.orgId, user.userId, {
      depth: spec.depth,
      maxResults: spec.limits.max_results,
      maxCredits: spec.limits.max_credits,
    });

    return {
      spec,
      feasibility: result.feasibility,
      needsConfirmation: result.needs_confirmation,
      ambiguities: result.ambiguities ?? [],
      unsupported: result.unsupported ?? [],
      confidence: result.confidence,
      estimate,
      provenance: {
        model: result.provenance.model,
        promptVersion: result.provenance.prompt_version,
        observedAt: result.provenance.observed_at,
        cached: result.provenance.cached ?? false,
      },
    };
  }

  /**
   * Fails in milliseconds when nothing is consuming the pool, instead of after the full
   * timeout. A deployment that forgot `WORKER_POOLS=...,interactive` looks exactly like this.
   */
  private async assertWorkerAvailable(): Promise<void> {
    const pool = this.config.JOBS_INTERACTIVE_POOL;
    if (await this.rpc.hasConsumers(streamFor(pool), groupFor(pool))) return;
    this.logger.error(
      { pool, errorClass: 'transient' },
      'no worker is consuming the interactive pool',
    );
    throw new AppError({
      code: 'research.parse_unavailable',
      httpStatus: 503,
      title: 'Parsing is temporarily unavailable',
      detail: 'No worker is available right now. Nothing was charged; try again in a moment.',
      errorClass: 'transient',
    });
  }

  /**
   * A sliding window per org: the current minute plus the tail of the previous one, so twice
   * the limit cannot slip through across a boundary. Parsing spends our tokens and none of the
   * user's credits, so this is the only brake on it.
   */
  private async checkRate(orgId: string): Promise<void> {
    const now = Date.now();
    const minute = Math.floor(now / 60_000);
    const elapsed = (now % 60_000) / 60_000;
    let used: number;
    try {
      const [current, previous] = await Promise.all([
        this.redis.incr(rateKey(orgId, minute)),
        this.redis.get(rateKey(orgId, minute - 1)),
      ]);
      await this.redis.expire(rateKey(orgId, minute), 120);
      used = current + Math.floor(Number(previous ?? 0) * (1 - elapsed));
    } catch (err) {
      // Redis is required for the RPC two lines later, so the request dies either way. Logged
      // as an error regardless: a spend limiter that is open is not a warning.
      this.logger.error({ err, orgId, errorClass: 'transient' }, 'parse rate limit check failed');
      return;
    }
    if (used > this.config.PARSE_RATE_PER_MINUTE) {
      throw new AppError({
        code: 'research.parse_rate_limited',
        httpStatus: 429,
        title: 'Too many parse requests',
        detail: `Up to ${this.config.PARSE_RATE_PER_MINUTE} per minute. Try again shortly.`,
        errorClass: 'rate_limited',
      });
    }
  }
}
