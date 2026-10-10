import { sql, withTenant } from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';

import { type Database, DB } from '../../infra/db/db.module';
import type { AuthUser, TenantInfo } from '../auth/auth.types';
import { CreditsService } from '../credits/credits.service';
import type {
  DashboardView,
  JobCounts,
  LeadCounts,
  ReachableCounts,
  RecentRun,
  SourceCount,
} from './dashboard.dto';

/**
 * Fields that mean "this lead can be contacted", which is what the headline counts measure.
 *
 * Matched with `in`, never `= any(...::text[])`. Drizzle expands a JS array inside a `sql`
 * template to `($1, $2, ...)`, which Postgres reads as a record: the cast fails with "cannot
 * cast type record to text[]". Raw postgres-js serialises the same array natively, so checking
 * the statement outside Drizzle answers a different question than the one being asked.
 */
const SOCIAL_FIELDS = ['instagram', 'facebook', 'linkedin', 'x', 'youtube', 'whatsapp'];

@Injectable()
export class DashboardService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly credits: CreditsService,
  ) {}

  /**
   * Everything the dashboard shows, in four round trips rather than a dozen.
   *
   * Four because they answer four unrelated questions and a single query joining leads, jobs,
   * field values and the ledger would be one unreadable statement that no index helps. They run
   * together: nothing here depends on anything else here.
   */
  async view(user: AuthUser, tenant: TenantInfo): Promise<DashboardView> {
    const ctx = { orgId: tenant.orgId, userId: user.userId };
    const [leadsAndReach, jobs, sources, recentRuns, balance, spent] = await Promise.all([
      this.leadsAndReach(ctx, tenant.workspaceId),
      this.jobs(ctx, tenant.workspaceId),
      this.sources(ctx, tenant.workspaceId),
      this.recentRuns(ctx, tenant.workspaceId),
      this.credits.balance(tenant.orgId, user.userId),
      this.spentLast30Days(ctx),
    ]);

    return {
      leads: leadsAndReach.leads,
      reachable: leadsAndReach.reachable,
      jobs,
      sources,
      recentRuns,
      credits: { balance, spentLast30Days: spent },
    };
  }

  /**
   * How many leads this workspace holds, how recently, and how many can be reached.
   *
   * One query for both because they walk the same rows. The contact fields are checked with
   * `exists` against `field_values` rather than joined: a lead has many values and a join would
   * multiply the rows being counted, which is the classic way a "total leads" figure silently
   * becomes "total values".
   */
  private async leadsAndReach(
    ctx: { orgId: string; userId: string },
    workspaceId: string,
  ): Promise<{ leads: LeadCounts; reachable: ReachableCounts }> {
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx.execute<{
        total: string;
        today: string;
        last7: string;
        last30: string;
        with_email: string;
        with_phone: string;
        with_website: string;
        with_social: string;
      }>(sql`
        with mine as (
          select id, company_id, created_at
            from app.leads
           where workspace_id = ${workspaceId}
        ),
        has as (
          select m.id,
                 exists (select 1 from app.field_values v
                          where v.entity_type = 'company' and v.entity_id = m.company_id
                            and v.is_current and v.field = 'email') as email,
                 exists (select 1 from app.field_values v
                          where v.entity_type = 'company' and v.entity_id = m.company_id
                            and v.is_current and v.field = 'phone') as phone,
                 exists (select 1 from app.field_values v
                          where v.entity_type = 'company' and v.entity_id = m.company_id
                            and v.is_current and v.field = 'website') as website,
                 exists (select 1 from app.field_values v
                          where v.entity_type = 'company' and v.entity_id = m.company_id
                            and v.is_current
                            and v.field in ${SOCIAL_FIELDS}) as social,
                 m.created_at
            from mine m
        )
        select count(*) as total,
               count(*) filter (where created_at >= date_trunc('day', now())) as today,
               count(*) filter (where created_at >= now() - interval '7 days') as last7,
               count(*) filter (where created_at >= now() - interval '30 days') as last30,
               count(*) filter (where email) as with_email,
               count(*) filter (where phone) as with_phone,
               count(*) filter (where website) as with_website,
               count(*) filter (where social) as with_social
          from has
      `),
    );
    const row = rows[0];
    return {
      leads: {
        total: Number(row?.total ?? 0),
        today: Number(row?.today ?? 0),
        last7Days: Number(row?.last7 ?? 0),
        last30Days: Number(row?.last30 ?? 0),
      },
      reachable: {
        withEmail: Number(row?.with_email ?? 0),
        withPhone: Number(row?.with_phone ?? 0),
        withWebsite: Number(row?.with_website ?? 0),
        withSocial: Number(row?.with_social ?? 0),
      },
    };
  }

  /** Runs by status, and when the last one finished. */
  private async jobs(
    ctx: { orgId: string; userId: string },
    workspaceId: string,
  ): Promise<JobCounts> {
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx.execute<{
        running: string;
        queued: string;
        completed: string;
        failed: string;
        last_finished: Date | null;
      }>(sql`
        select count(*) filter (where status = 'running') as running,
               count(*) filter (where status = 'queued') as queued,
               count(*) filter (where status = 'completed') as completed,
               count(*) filter (where status in ('failed', 'cancelled')) as failed,
               max(finished_at) as last_finished
          from app.research_jobs
         where workspace_id = ${workspaceId}
      `),
    );
    const row = rows[0];
    return {
      running: Number(row?.running ?? 0),
      queued: Number(row?.queued ?? 0),
      completed: Number(row?.completed ?? 0),
      failed: Number(row?.failed ?? 0),
      lastFinishedAt: row?.last_finished ? new Date(row.last_finished).toISOString() : null,
    };
  }

  /**
   * Which sources the workspace's facts actually came from.
   *
   * Counted over values in force for companies this workspace holds, not over the whole graph:
   * the graph is shared, and a number that includes another customer's crawls would be a lie
   * dressed as a statistic.
   */
  private async sources(
    ctx: { orgId: string; userId: string },
    workspaceId: string,
  ): Promise<SourceCount[]> {
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx.execute<{ source: string; values: string }>(sql`
        select s.key as source, count(*) as values
          from app.leads l
          join app.field_values v
            on v.entity_type = 'company' and v.entity_id = l.company_id and v.is_current
          join app.sources s on s.id = v.source_id
         where l.workspace_id = ${workspaceId}
         group by s.key
         order by count(*) desc
      `),
    );
    return rows.map((r) => ({ source: r.source, values: Number(r.values) }));
  }

  /** The last five runs, with what each delivered (ADR-0015: found, and new). */
  private async recentRuns(
    ctx: { orgId: string; userId: string },
    workspaceId: string,
  ): Promise<RecentRun[]> {
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx.execute<{
        id: string;
        raw_query: string;
        status: string;
        created_at: Date;
        leads: string;
        new_leads: string;
      }>(sql`
        select j.id, s.raw_query, j.status, j.created_at,
               count(rjl.lead_id) as leads,
               count(rjl.lead_id) filter (where rjl.is_new) as new_leads
          from app.research_jobs j
          join app.searches s on s.id = j.search_id
          left join app.research_job_leads rjl on rjl.research_job_id = j.id
         where j.workspace_id = ${workspaceId}
         group by j.id, s.raw_query, j.status, j.created_at
         order by j.created_at desc
         limit 5
      `),
    );
    return rows.map((r) => ({
      id: r.id,
      rawQuery: r.raw_query,
      status: r.status,
      createdAt: new Date(r.created_at).toISOString(),
      leads: Number(r.leads),
      newLeads: Number(r.new_leads),
    }));
  }

  /**
   * Credits spent in the last 30 days.
   *
   * From the ledger's negative entries, not from `research_jobs.credits_used`: the ledger is what
   * the balance is computed from, and a figure derived from anywhere else can disagree with the
   * number beside it (ADR-0014).
   */
  private async spentLast30Days(ctx: { orgId: string; userId: string }): Promise<number> {
    const rows = await withTenant(this.db, ctx, (tx) =>
      tx.execute<{ spent: string }>(sql`
        select coalesce(-sum(delta), 0) as spent
          from app.credit_ledger
         where reason = 'consume' and created_at >= now() - interval '30 days'
      `),
    );
    return Number(rows[0]?.spent ?? 0);
  }
}
