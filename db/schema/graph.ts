import { sql } from 'drizzle-orm';
import {
  boolean,
  char,
  check,
  doublePrecision,
  foreignKey,
  index,
  integer,
  jsonb,
  real,
  text,
  unique,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';

import { app, createdAt, updatedAt } from './common.ts';
import { geoKind } from './enums.ts';

/**
 * Global company graph (docs/04, ADR-0007): shared across tenants, filled only from public sources
 * and official APIs by workers. Never holds tenant-private data. Readable by both app roles,
 * writable by app_worker only (RLS policies in the custom migration).
 */

/** Industry taxonomy (seeded). `google_types` maps to Places API (New) place types. */
export const industries = app.table(
  'industries',
  {
    id: uuid('id').primaryKey(),
    parentId: uuid('parent_id'),
    slug: text('slug').notNull(),
    name: text('name').notNull(),
    naicsCode: text('naics_code'),
    synonyms: text('synonyms')
      .array()
      .notNull()
      .default(sql`'{}'::text[]`),
    googleTypes: text('google_types')
      .array()
      .notNull()
      .default(sql`'{}'::text[]`),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    uniqueIndex('industries_slug_key').on(t.slug),
    foreignKey({ name: 'industries_parent_fk', columns: [t.parentId], foreignColumns: [t.id] }),
    check('industries_slug_format', sql`${t.slug} ~ '^[a-z0-9]+(-[a-z0-9]+)*$'`),
  ],
);

/** Countries, states, cities and localities with bounding boxes for search tiling (seeded). */
export const geoAreas = app.table(
  'geo_areas',
  {
    id: uuid('id').primaryKey(),
    parentId: uuid('parent_id'),
    kind: geoKind('kind').notNull(),
    country: char('country', { length: 2 }).notNull(),
    slug: text('slug').notNull(),
    name: text('name').notNull(),
    aliases: text('aliases')
      .array()
      .notNull()
      .default(sql`'{}'::text[]`),
    minLat: doublePrecision('min_lat').notNull(),
    minLng: doublePrecision('min_lng').notNull(),
    maxLat: doublePrecision('max_lat').notNull(),
    maxLng: doublePrecision('max_lng').notNull(),
    population: integer('population'),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    uniqueIndex('geo_areas_country_kind_slug_key').on(t.country, t.kind, t.slug),
    foreignKey({ name: 'geo_areas_parent_fk', columns: [t.parentId], foreignColumns: [t.id] }),
    index('geo_areas_parent_idx').on(t.parentId),
    check('geo_areas_country_format', sql`${t.country} ~ '^[A-Z]{2}$'`),
    check('geo_areas_slug_format', sql`${t.slug} ~ '^[a-z0-9]+(-[a-z0-9]+)*$'`),
    // min < max rules out boxes crossing the antimeridian; none of our regions do (ADR-0007).
    check(
      'geo_areas_bbox_valid',
      sql`${t.minLat} between -90 and 90 and ${t.maxLat} between -90 and 90
          and ${t.minLng} between -180 and 180 and ${t.maxLng} between -180 and 180
          and ${t.minLat} < ${t.maxLat} and ${t.minLng} < ${t.maxLng}`,
    ),
  ],
);

export const companies = app.table(
  'companies',
  {
    id: uuid('id').primaryKey(),
    canonicalName: text('canonical_name').notNull(),
    normalizedName: text('normalized_name').notNull(),
    /** Bare lower-case host, no scheme/port/path and no `www.` prefix (checked); unique. */
    primaryDomain: text('primary_domain'),
    country: char('country', { length: 2 }),
    state: text('state'),
    city: text('city'),
    industryId: uuid('industry_id').references(() => industries.id),
    companyStatus: text('company_status'),
    /** Computed best values per field (docs/04 best-value computation); never edited by hand. */
    best: jsonb('best').notNull().default({}),
    confidence: real('confidence'),
    mergedIntoId: uuid('merged_into_id'),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    // Plain unique (NULLs never conflict), so upserts can use ON CONFLICT (primary_domain).
    unique('companies_primary_domain_key').on(t.primaryDomain),
    index('companies_geo_industry_idx').on(t.country, t.city, t.industryId),
    foreignKey({
      name: 'companies_merged_into_fk',
      columns: [t.mergedIntoId],
      foreignColumns: [t.id],
    }),
    check(
      'companies_domain_format',
      sql`${t.primaryDomain} ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$' and ${t.primaryDomain} not like 'www.%'`,
    ),
    check('companies_country_format', sql`${t.country} ~ '^[A-Z]{2}$'`),
    check('companies_confidence_range', sql`${t.confidence} between 0 and 1`),
  ],
);

export const companyDomains = app.table(
  'company_domains',
  {
    id: uuid('id').primaryKey(),
    companyId: uuid('company_id')
      .notNull()
      .references(() => companies.id, { onDelete: 'cascade' }),
    domain: text('domain').notNull(),
    isPrimary: boolean('is_primary').notNull().default(false),
    /** Shared hosts (e.g. business.site, linktr.ee) that must never merge companies. */
    isPlatform: boolean('is_platform').notNull().default(false),
    createdAt: createdAt(),
  },
  (t) => [
    uniqueIndex('company_domains_domain_key')
      .on(t.domain)
      .where(sql`not ${t.isPlatform}`),
    unique('company_domains_company_domain_key').on(t.companyId, t.domain),
    check(
      'company_domains_domain_format',
      sql`${t.domain} ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$' and ${t.domain} not like 'www.%'`,
    ),
  ],
);

export const companyLocations = app.table(
  'company_locations',
  {
    id: uuid('id').primaryKey(),
    companyId: uuid('company_id')
      .notNull()
      .references(() => companies.id, { onDelete: 'cascade' }),
    address: text('address'),
    city: text('city'),
    state: text('state'),
    postalCode: text('postal_code'),
    country: char('country', { length: 2 }),
    lat: doublePrecision('lat'),
    lng: doublePrecision('lng'),
    googlePlaceId: text('google_place_id'),
    phoneE164: text('phone_e164'),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    // Plain unique (NULLs never conflict), so upserts can use ON CONFLICT (google_place_id).
    unique('company_locations_place_key').on(t.googlePlaceId),
    index('company_locations_company_idx').on(t.companyId),
    check('company_locations_phone_e164', sql`${t.phoneE164} ~ '^[+][1-9][0-9]{6,14}$'`),
    check('company_locations_country_format', sql`${t.country} ~ '^[A-Z]{2}$'`),
    check(
      'company_locations_latlng',
      sql`(${t.lat} is null) = (${t.lng} is null) and (${t.lat} is null or (${t.lat} between -90 and 90 and ${t.lng} between -180 and 180))`,
    ),
  ],
);
