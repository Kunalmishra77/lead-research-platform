import { creditRates, sql, withTenant } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { uuidv7 } from 'uuidv7';
import { z } from 'zod';

import { pgCode } from '../../common/db/pg-error';
import { AppError } from '../../common/errors/app-error';
import { type Database, DB } from '../../infra/db/db.module';
import { type ResearchDepth, researchDepthSchema } from './credits.dto';

/** Rates live in app.credit_rates (docs/11): data, never hardcoded. Cached briefly per process. */
const RATES_TTL_MS = 60_000;
/** Raised by app.credit_reserve / app.credit_post when the balance would go negative. */
const INSUFFICIENT_CREDITS = 'LF402';
/** research_jobs.credits_* are integer columns; the functions refuse anything larger. */
const MAX_CREDITS = 2_147_483_647;

const amountSchema = z.number().int().min(1).max(MAX_CREDITS);
const estimateInputSchema = z.object({
  depth: researchDepthSchema,
  maxResults: z.number().int().min(1).max(100_000),
  maxCredits: z.number().int().min(1).max(MAX_CREDITS),
});
export type EstimateInput = z.infer<typeof estimateInputSchema>;

/** Pure part of the estimate, so the rule is testable without a database. */
export function estimateFor(creditsPerLead: number, input: EstimateInput): CreditEstimate {
  const byResults = creditsPerLead * input.maxResults;
  return {
    depth: input.depth,
    meter: DEPTH_METERS[input.depth],
    creditsPerLead,
    maxResults: input.maxResults,
    reserve: Math.min(byResults, input.maxCredits),
    cappedByBudget: input.maxCredits < byResults,
  };
}

export const DEPTH_METERS: Record<ResearchDepth, string> = {
  quick: 'research_quick',
  standard: 'research_standard',
  deep: 'research_deep',
};

export interface CreditEstimate {
  depth: ResearchDepth;
  meter: string;
  creditsPerLead: number;
  maxResults: number;
  /** What the job reserves: rate x maxResults, capped by the spec's own credit limit. */
  reserve: number;
  /** True when `limits.max_credits` (not the result cap) decided the reservation. */
  cappedByBudget: boolean;
}

@Injectable()
export class CreditsService {
  private rates = new Map<string, number>();
  private ratesLoadedAt = 0;

  constructor(@Inject(DB) private readonly db: Database) {}

  /** Credits per unit for every meter; cached for a minute so a rate change rolls out by itself. */
  async getRates(orgId: string, userId: string | null): Promise<ReadonlyMap<string, number>> {
    if (this.rates.size > 0 && Date.now() - this.ratesLoadedAt < RATES_TTL_MS) {
      return new Map(this.rates);
    }
    const rows = await withTenant(this.db, { orgId, userId }, (tx) =>
      tx
        .select({ meter: creditRates.meter, credits: creditRates.creditsPerUnit })
        .from(creditRates),
    );
    this.rates = new Map(rows.map((r) => [r.meter, r.credits]));
    this.ratesLoadedAt = Date.now();
    return new Map(this.rates);
  }

  async rateFor(orgId: string, userId: string | null, meter: string): Promise<number> {
    const rate = (await this.getRates(orgId, userId)).get(meter);
    if (rate === undefined) {
      throw new AppError({
        code: 'credits.unknown_meter',
        httpStatus: 500,
        title: 'Unknown meter',
        detail: `No credit rate configured for ${meter}`,
      });
    }
    return rate;
  }

  /** What a run would cost at most (docs/09: the estimate is shown before anything is spent). */
  async estimate(
    orgId: string,
    userId: string | null,
    input: EstimateInput,
  ): Promise<CreditEstimate> {
    const spec = estimateInputSchema.parse(input);
    return estimateFor(await this.rateFor(orgId, userId, DEPTH_METERS[spec.depth]), spec);
  }

  async balance(orgId: string, userId: string | null): Promise<number> {
    const rows = await withTenant(this.db, { orgId, userId }, (tx) =>
      tx.execute<{ credits_balance: string }>(
        sql`select credits_balance from app.organizations where id = ${orgId}`,
      ),
    );
    return Number(rows[0]?.credits_balance ?? 0);
  }

  /**
   * Holds credits for a job before it starts. Idempotent per job, so a retried create never
   * debits twice. 402 when the balance is too low (docs/05 credits in API).
   */
  async reserve(
    orgId: string,
    userId: string | null,
    jobId: string,
    amount: number,
  ): Promise<number> {
    amountSchema.parse(amount);
    try {
      const rows = await withTenant(this.db, { orgId, userId }, (tx) =>
        tx.execute<{ credit_reserve: string }>(
          sql`select app.credit_reserve(${jobId}::uuid, ${amount}::bigint, ${uuidv7()}::uuid)`,
        ),
      );
      return Number(rows[0]?.credit_reserve ?? 0);
    } catch (err) {
      if (pgCode(err) === INSUFFICIENT_CREDITS) {
        throw new AppError({
          code: 'credits.insufficient',
          httpStatus: 402,
          title: 'Not enough credits',
          detail: `This run needs ${String(amount)} credits. Top up and try again.`,
          errorClass: 'budget_exhausted',
          cause: err,
        });
      }
      throw ledgerError(err, 'reserve');
    }
  }

  /**
   * Books what the job delivered and returns the unused reservation. Safe to call again: only
   * usage that is not booked yet is charged, so late usage events are never free or double-billed.
   */
  async settle(
    orgId: string,
    userId: string | null,
    jobId: string,
  ): Promise<{ consumed: number; released: number }> {
    try {
      const rows = await withTenant(this.db, { orgId, userId }, (tx) =>
        tx.execute<{ consumed: string; released: string }>(
          sql`select * from app.credit_settle(${jobId}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
        ),
      );
      return { consumed: Number(rows[0]?.consumed ?? 0), released: Number(rows[0]?.released ?? 0) };
    } catch (err) {
      throw ledgerError(err, 'settle');
    }
  }
}

/** Turns the ledger functions' SQLSTATEs into typed errors (docs/12: no raw 500s). */
function ledgerError(err: unknown, operation: 'reserve' | 'settle'): unknown {
  switch (pgCode(err)) {
    case '42501':
      return new AppError({
        code: 'credits.forbidden',
        httpStatus: 403,
        title: 'Job is not in this workspace',
        cause: err,
      });
    case '22023':
      return new AppError({
        code: `credits.${operation}_invalid`,
        httpStatus: 409,
        title: operation === 'settle' ? 'Job has no reservation' : 'Invalid reservation amount',
        errorClass: 'invalid_input',
        cause: err,
      });
    default:
      return err;
  }
}
