/** Source registry seed (docs/08 source matrix, MVP sources). All start disabled. */
export interface SourceSeed {
  key: string;
  name: string;
  type: 'api' | 'crawl' | 'registry' | 'provider' | 'user';
  tosClass: 'green' | 'amber' | 'red';
  /** Freshness: when a value is worth re-checking. */
  defaultTtlDays: number;
  /**
   * Retention: when a value must be deleted because the provider's terms say so. Only set it
   * where a licence actually requires it; everything else is ours to keep.
   */
  retentionDays?: number;
}

export const SOURCE_SEEDS: SourceSeed[] = [
  {
    key: 'google_places',
    name: 'Google Places API (New)',
    type: 'api',
    tosClass: 'green',
    defaultTtlDays: 30,
    // Google's terms allow 30 days and then require deletion (ADR-0011). This is the only
    // source in the list with a deletion obligation, which is why the column exists.
    retentionDays: 30,
  },
  {
    key: 'serp',
    name: 'SERP API (vendor TBD, Phase 2)',
    type: 'api',
    tosClass: 'green',
    defaultTtlDays: 14,
  },
  {
    key: 'website',
    name: 'Company websites (own crawler)',
    type: 'crawl',
    tosClass: 'amber',
    defaultTtlDays: 30,
  },
  {
    key: 'tech_detector',
    name: 'Passive technology detection',
    type: 'crawl',
    tosClass: 'green',
    defaultTtlDays: 30,
  },
  { key: 'dns', name: 'DNS (MX/TXT)', type: 'api', tosClass: 'green', defaultTtlDays: 30 },
  {
    key: 'companies_house',
    name: 'UK Companies House API',
    type: 'registry',
    tosClass: 'green',
    defaultTtlDays: 90,
  },
  {
    key: 'opencorporates',
    name: 'OpenCorporates API',
    type: 'registry',
    tosClass: 'green',
    defaultTtlDays: 90,
  },
  { key: 'sec_edgar', name: 'SEC EDGAR', type: 'registry', tosClass: 'green', defaultTtlDays: 90 },
  {
    key: 'ats_boards',
    name: 'ATS public job boards',
    type: 'api',
    tosClass: 'green',
    defaultTtlDays: 7,
  },
  {
    key: 'email_verifier',
    name: 'Email verification API (vendor TBD, Phase 4)',
    type: 'provider',
    tosClass: 'green',
    defaultTtlDays: 90,
  },
  {
    key: 'user_import',
    name: 'User CSV import / CRM pull',
    type: 'user',
    tosClass: 'green',
    defaultTtlDays: 365,
  },
];
