/** Seed data quality (no database): taxonomy and geography stay consistent as they grow. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { loadGeoSeeds, parentKind } from '../seeds/geo.ts';
import { GENERIC_GOOGLE_TYPES, INDUSTRY_SEEDS } from '../seeds/industries.ts';

const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

describe('industry taxonomy seed', () => {
  const googleTypes = new Set(
    (
      JSON.parse(
        readFileSync(
          resolve(import.meta.dirname, '..', 'seeds', 'data', 'google-place-types-table-a.json'),
          'utf8',
        ),
      ) as { types: string[] }
    ).types,
  );

  it('has ~300 categories under 20 sectors with unique, valid slugs', () => {
    const slugs = INDUSTRY_SEEDS.map((s) => s.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    expect(slugs.every((s) => SLUG.test(s))).toBe(true);
    expect(INDUSTRY_SEEDS.filter((s) => !s.parent)).toHaveLength(20);
    expect(INDUSTRY_SEEDS.filter((s) => s.parent).length).toBeGreaterThanOrEqual(280);
  });

  it('parents exist and appear before their children', () => {
    const seen = new Set<string>();
    for (const s of INDUSTRY_SEEDS) {
      if (s.parent) expect(seen.has(s.parent), s.slug).toBe(true);
      seen.add(s.slug);
    }
  });

  it('maps only to Google Places (New) Table A types', () => {
    const invalid = INDUSTRY_SEEDS.flatMap((s) =>
      s.googleTypes.filter((t) => !googleTypes.has(t)).map((t) => `${s.slug}: ${t}`),
    );
    expect(invalid).toEqual([]);
  });

  it('never maps to catch-all types that would return every business in a tile', () => {
    const generic = new Set<string>(GENERIC_GOOGLE_TYPES);
    const offenders = INDUSTRY_SEEDS.filter((s) => s.googleTypes.some((t) => generic.has(t)));
    expect(offenders.map((s) => s.slug)).toEqual([]);
  });

  it('each name and synonym points to exactly one category (unambiguous query expansion)', () => {
    const owners = new Map<string, string[]>();
    for (const s of INDUSTRY_SEEDS) {
      for (const term of new Set([s.name, ...s.synonyms].map((t) => t.toLowerCase()))) {
        owners.set(term, [...(owners.get(term) ?? []), s.slug]);
      }
    }
    const ambiguous = [...owners].filter(([, slugs]) => slugs.length > 1);
    expect(ambiguous).toEqual([]);
  });

  it('covers India-first SMB categories', () => {
    const slugs = new Set(INDUSTRY_SEEDS.map((s) => s.slug));
    for (const slug of [
      'kirana-store',
      'sweet-shop',
      'dhaba',
      'coaching-centre',
      'chartered-accountant',
      'tiffin-service',
    ]) {
      expect(slugs.has(slug), slug).toBe(true);
    }
  });
});

describe('geography seed', () => {
  const geo = loadGeoSeeds();
  const byKey = new Map(geo.areas.map((a) => [`${a.kind}:${a.slug}`, a]));

  it('records its source and licence', () => {
    expect(geo.license).toMatch(/OpenStreetMap contributors, ODbL/);
  });

  it('city slugs are state-qualified (same-named cities exist in different states)', () => {
    for (const a of geo.areas.filter((x) => x.kind === 'city')) {
      expect(a.slug.endsWith(`-${a.parent ?? ''}`), a.slug).toBe(true);
    }
  });

  it('has India, 36 states/UTs and ~150 cities with unique keys', () => {
    expect(geo.areas.filter((a) => a.kind === 'country')).toHaveLength(1);
    expect(geo.areas.filter((a) => a.kind === 'state')).toHaveLength(36);
    expect(geo.areas.filter((a) => a.kind === 'city').length).toBeGreaterThanOrEqual(150);
    expect(byKey.size).toBe(geo.areas.length);
  });

  it('every bbox is valid, and cities are city-sized', () => {
    for (const a of geo.areas) {
      const [minLat, minLng, maxLat, maxLng] = a.bbox;
      expect(minLat < maxLat && minLng < maxLng, a.name).toBe(true);
      if (a.kind === 'city') {
        expect(Math.max(maxLat - minLat, maxLng - minLng), a.name).toBeLessThanOrEqual(0.55);
      }
    }
  });

  it('every area has a parent of the right kind, and cities sit inside their state', () => {
    for (const a of geo.areas) {
      const kind = parentKind(a.kind);
      if (!kind) continue;
      const parent = byKey.get(`${kind}:${a.parent ?? ''}`);
      expect(parent, `${a.name} -> ${String(a.parent)}`).toBeDefined();
      if (a.kind !== 'city' || !parent) continue;
      // The city centre must lie inside the state box (radius boxes may spill over borders).
      const lat = (a.bbox[0] + a.bbox[2]) / 2;
      const lng = (a.bbox[1] + a.bbox[3]) / 2;
      expect(
        lat >= parent.bbox[0] &&
          lat <= parent.bbox[2] &&
          lng >= parent.bbox[1] &&
          lng <= parent.bbox[3],
        `${a.name} centre outside ${parent.name}`,
      ).toBe(true);
    }
  });

  it('Delhi uses the NCT boundary', () => {
    const delhi = byKey.get('city:delhi-delhi');
    expect(delhi?.bboxSource).toBe('boundary');
    expect(delhi?.aliases).toContain('New Delhi');
  });
});
