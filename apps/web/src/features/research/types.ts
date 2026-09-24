import type { ResearchSpec } from '@leadforge/contracts';

/**
 * What the research endpoints return.
 *
 * Hand-written rather than generated, which docs/09 would rather they were not ("API types
 * generated from OpenAPI; no hand-written duplicates"). There is no generator wired up yet, so
 * these mirror `apps/api/src/modules/research/research.dto.ts` and will drift the day someone
 * changes it without changing this. The spec itself is the exception and comes from
 * `@leadforge/contracts`, which is generated — so the one type that is a real contract between
 * the two sides has a single source.
 */

export type JobStatus =
  'queued' | 'planning' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';

export const TERMINAL_STATUSES: readonly JobStatus[] = [
  'completed',
  'failed',
  'cancelled',
  'paused',
];

export type Depth = 'quick' | 'standard' | 'deep';

export interface CreditEstimate {
  depth: Depth;
  meter: string;
  creditsPerLead: number;
  maxResults: number;
  /** What will be held for the run; the rest comes back when it ends. */
  reserve: number;
  /** True when `max_credits` decided the number, not `max_results`. */
  cappedByBudget: boolean;
}

export interface Feasibility {
  filter: string;
  mode: 'directly_searchable' | 'post_filter' | 'estimate_only' | 'unsupported';
  note?: string;
}

export interface ParseResult {
  spec: ResearchSpec;
  feasibility: Feasibility[];
  needsConfirmation: boolean;
  ambiguities: string[];
  unsupported: string[];
  confidence: number;
  estimate: CreditEstimate;
  provenance: { model: string; promptVersion: string; observedAt: string; cached: boolean };
}

export interface JobCredits {
  reserved: number;
  used: number;
  balance: number;
}

/** The counters workers write. A closed set (`PROGRESS_COUNTERS` in the worker). */
export interface JobProgress {
  candidates?: number;
  leads?: number;
  values?: number;
  message?: string;
}

export interface ResearchJobView {
  id: string;
  status: JobStatus;
  depth: string;
  rawQuery: string;
  spec: ResearchSpec;
  progress: JobProgress | null;
  credits: JobCredits;
  errorClass: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface ResearchJobListItem {
  id: string;
  status: JobStatus;
  depth: string;
  rawQuery: string;
  creditsReserved: number;
  creditsUsed: number;
  createdAt: string;
  finishedAt: string | null;
}

export interface ResearchPage {
  items: ResearchJobListItem[];
  nextCursor: string | null;
}

export interface LeadValue {
  field: string;
  value: unknown;
  /** `app.sources.key`. What attribution keys on (ADR-0011). */
  source: string;
  sourceUrl: string;
  observedAt: string;
  method: string;
  derivation: string | null;
  confidence: number;
}

export interface LeadView {
  id: string;
  companyId: string;
  status: string;
  createdAt: string;
  name: string;
  domain: string | null;
  city: string | null;
  country: string | null;
  address: string | null;
  phone: string | null;
  googlePlaceId: string | null;
  values: LeadValue[];
}

export interface LeadsPage {
  items: LeadView[];
  nextCursor: string | null;
  /** Every source that contributed a value on this page, for the attribution block. */
  sources: string[];
}

/** The live event the SSE stream sends (`ProgressEvent` in packages/contracts). */
export interface ProgressMessage {
  job_id: string;
  stage: string;
  status: 'running' | 'completed' | 'failed' | 'paused' | 'cancelled';
  counts: Record<string, number>;
  credits_used: number;
  message?: string | null;
  error_class?: string | null;
  ts: string;
}

export function valueOf(lead: LeadView, field: string): LeadValue | undefined {
  return lead.values.find((v) => v.field === field);
}
