/**
 * What the dashboard shows, and deliberately nothing more.
 *
 * Every number here is read from something the system already recorded: a lead row, a field
 * value with its source, a job's status, a ledger entry. There is no lead score, no verification
 * rate and no hot/warm/cold split, because scoring (Phase 6) and verification (Phase 4) are not
 * built — and a dashboard that invents those is worse than one that omits them. CLAUDE.md's
 * provenance rule is about values in the grid, but the same honesty applies to a headline figure
 * a person will act on.
 */
export interface DashboardView {
  leads: LeadCounts;
  /** How many of this workspace's leads can actually be contacted, which is the point of them. */
  reachable: ReachableCounts;
  jobs: JobCounts;
  credits: CreditSummary;
  /** Where the stored facts came from, newest-known first. */
  sources: SourceCount[];
  recentRuns: RecentRun[];
}

export interface LeadCounts {
  total: number;
  today: number;
  last7Days: number;
  last30Days: number;
}

/**
 * Counted over the workspace's whole book, not over the last run.
 *
 * `withEmail` is the number that decides whether this product is useful: a name and a map pin is
 * something anyone can get, and an address someone will answer is not.
 */
export interface ReachableCounts {
  withEmail: number;
  withPhone: number;
  withWebsite: number;
  withSocial: number;
}

export interface JobCounts {
  running: number;
  queued: number;
  completed: number;
  failed: number;
  /** When the last run finished, so "nothing is happening" and "nothing ever ran" differ. */
  lastFinishedAt: string | null;
}

export interface CreditSummary {
  balance: number;
  /** Spent in the last 30 days, from the ledger rather than from job rows. */
  spentLast30Days: number;
}

export interface SourceCount {
  source: string;
  /** Field values currently in force that this source is responsible for. */
  values: number;
}

export interface RecentRun {
  id: string;
  rawQuery: string;
  status: string;
  createdAt: string;
  /** Leads this run surfaced, and how many were new to the workspace (ADR-0015). */
  leads: number;
  newLeads: number;
}
