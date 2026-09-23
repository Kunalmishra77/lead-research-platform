/**
 * Credit rates and plan allowances (docs/11). These are starting assumptions stored as data, never
 * hardcoded in services: the API reads them to estimate a run and to bill delivered units.
 */
export interface CreditRateSeed {
  meter: string;
  creditsPerUnit: number;
  unit: string;
  description: string;
}

export const CREDIT_RATE_SEEDS: CreditRateSeed[] = [
  {
    meter: 'research_quick',
    creditsPerUnit: 1,
    unit: 'delivered new lead',
    description: 'Quick depth: discovery fields only',
  },
  {
    meter: 'research_standard',
    creditsPerUnit: 3,
    unit: 'delivered new lead',
    description: 'Standard depth: + website profile, socials, tech, contacts',
  },
  {
    meter: 'research_deep',
    creditsPerUnit: 8,
    unit: 'delivered new lead',
    description: 'Deep depth: + people, hiring, news, AI summary, multi-source verification',
  },
  {
    meter: 'enrich_module',
    creditsPerUnit: 1,
    unit: 'module run per record',
    description: 'Enrichment module (Phase 5); per-module overrides come with the module config',
  },
  {
    meter: 'ai_report',
    creditsPerUnit: 5,
    unit: 'report',
    description: 'AI research report (Phase 6); longer reports cost more per their own config',
  },
  {
    meter: 'email_verify',
    creditsPerUnit: 1,
    unit: 'email',
    description: 'Mailbox verification (Phase 4)',
  },
  {
    meter: 'export_overage',
    creditsPerUnit: 1,
    unit: '100 rows beyond the plan limit',
    description: 'Export rows beyond the plan limit (Phase 7)',
  },
  {
    meter: 'search_stored',
    creditsPerUnit: 0,
    unit: 'query over stored data',
    description: 'Searching already-stored leads is free (rate limited)',
  },
];

export interface PlanSeed {
  plan: string;
  name: string;
  signupCredits: number;
  monthlyCredits: number;
  seats: number;
}

/** Phase 2 uses `signup_credits` only; renewals and payments arrive in Phase 7. */
export const PLAN_SEEDS: PlanSeed[] = [
  { plan: 'free', name: 'Free', signupCredits: 50, monthlyCredits: 0, seats: 1 },
  { plan: 'starter', name: 'Starter', signupCredits: 0, monthlyCredits: 1500, seats: 2 },
  { plan: 'growth', name: 'Growth', signupCredits: 0, monthlyCredits: 6000, seats: 5 },
  { plan: 'pro', name: 'Pro / Agency', signupCredits: 0, monthlyCredits: 20000, seats: 10 },
  { plan: 'enterprise', name: 'Enterprise', signupCredits: 0, monthlyCredits: 0, seats: 25 },
];
