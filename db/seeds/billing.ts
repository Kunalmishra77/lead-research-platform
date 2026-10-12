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
  // Per delivered lead, by the source that discovered it. Charging for discovery follows what
  // discovery costs us, and since ADR-0017 that varies by two orders of magnitude: a lead from
  // the bulk place index costs nothing, where a Google Places lead cost about Rs 2.21 measured
  // over a real sweep (345 calls at $0.035 for 528 leads).
  //
  // A source with no rate of its own falls back to the depth meter, so a source added later
  // charges something until somebody prices it. Free has to be a decision, never a default.
  {
    meter: 'lead_overture',
    creditsPerUnit: 0,
    unit: 'delivered new lead',
    description: 'Overture Maps Places: a monthly bulk release read off local disk, no API cost',
  },
  {
    meter: 'lead_osm',
    creditsPerUnit: 0,
    unit: 'delivered new lead',
    description: 'OpenStreetMap: a bulk extract read off local disk, no API cost',
  },
  {
    meter: 'lead_google_places',
    creditsPerUnit: 2,
    unit: 'delivered new lead',
    // One credit is 20,000 micros (~Rs 1.68), so two covers the Rs 2.21 a Places lead cost on
    // the gyms sweep and leaves a margin. One would have been sold at a loss.
    description: 'Google Places API (New): billed per call, about Rs 2.21 per delivered lead',
  },
  {
    meter: 'research_quick',
    creditsPerUnit: 1,
    unit: 'delivered new lead',
    description:
      'Quick depth fallback: used when the discovering source has no per-lead rate of its own',
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
