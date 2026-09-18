/** Source registry seed (docs/08 source matrix, MVP sources). All start disabled. */
export interface SourceSeed {
  key: string;
  name: string;
  type: 'api' | 'crawl' | 'registry' | 'provider' | 'user';
  tosClass: 'green' | 'amber' | 'red';
  defaultTtlDays: number;
}

export const SOURCE_SEEDS: SourceSeed[] = [
  {
    key: 'google_places',
    name: 'Google Places API (New)',
    type: 'api',
    tosClass: 'green',
    defaultTtlDays: 30,
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
