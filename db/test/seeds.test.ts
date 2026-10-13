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

describe('Overture category mapping', () => {
  const read = (name: string): unknown =>
    JSON.parse(readFileSync(resolve(import.meta.dirname, '..', 'seeds', 'data', name), 'utf8'));

  const vocabulary = read('overture-categories-in.json') as {
    release: string;
    categories: { category: string; places_in_india: number }[];
  };
  const known = new Map(vocabulary.categories.map((c) => [c.category, c.places_in_india]));
  const { map } = read('overture-category-map.json') as { map: Record<string, string[]> };

  it('records the release it was read from', () => {
    expect(vocabulary.release).toMatch(/^\d{4}-\d{2}-\d{2}/);
  });

  it('names only categories the dataset publishes', () => {
    // The point of the snapshot. A category Overture drops or renames between releases becomes
    // a filter that matches nothing, and a search returning zero leads looks identical to an
    // area with no businesses in it. This turns that into a failing build.
    const invalid = Object.entries(map).flatMap(([slug, cats]) =>
      cats.filter((c) => !known.has(c)).map((c) => `${slug}: ${c}`),
    );
    expect(invalid).toEqual([]);
  });

  it('covers every sector and category in the taxonomy', () => {
    const missing = INDUSTRY_SEEDS.filter((s) => map[s.slug] === undefined).map((s) => s.slug);
    expect(missing).toEqual([]);
  });

  it('leaves a category unmapped rather than mapping it to something broader', () => {
    // 24 entries are deliberately empty because Overture has no equivalent -- there is no EV
    // dealer category, so `ev-dealer` maps to nothing and the connector falls back to searching
    // names. Mapping it to `vehicle_dealer` would have answered "EV dealers in Delhi" with
    // 70,109 car showrooms, which is worse than admitting the gap.
    const empty = Object.entries(map).filter(([, cats]) => cats.length === 0);
    expect(empty.length).toBeGreaterThan(0);
    expect(empty.length).toBeLessThan(INDUSTRY_SEEDS.length / 4);
  });

  it('maps the categories that carry the most of the market', () => {
    // Measured on Delhi: mapping only what Google's type names happened to match reached 19.4%
    // of businesses, and this mapping reaches 96.2%. These are the mappings that moved it, so
    // a refactor that quietly drops one should fail here.
    for (const [slug, expected] of [
      ['retail', 'shopping'],
      ['professional-services', 'professional_service'],
      ['finance', 'financial_service'],
      ['construction-and-real-estate', 'real_estate_service'],
      ['education', 'education'],
      ['gym', 'gym'],
      // Deliberately NOT widened to `fashion_and_apparel_store`: that parent also holds
      // jewelry_store, shoe_store and eyewear_store, so a request for clothing shops would
      // come back with jewellers and opticians in it.
      ['clothing-store', 'clothing_store'],
    ] as const) {
      expect(map[slug], slug).toContain(expected);
    }
  });

  it('translates the names Overture does not share with Google', () => {
    // Each of these was wrong before it was checked against the data: Overture has no
    // `car_repair`, no `university`, no `physiotherapist` and no `bicycle_store`.
    for (const [slug, expected] of [
      ['car-repair', 'automotive_repair'],
      ['university', 'college_university'],
      ['physiotherapy-clinic', 'physical_therapy'],
      ['bicycle-store', 'bike_store'],
      ['hindu-temple', 'hindu_place_of_worship'],
      ['packers-movers', 'mover'],
    ] as const) {
      expect(map[slug], slug).toContain(expected);
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

  it('every state and union territory carries its ISO 3166-2 code', () => {
    // Sources report the code far more often than the name: 870 of the live graph's company
    // state values were `HR` and 245 were `Haryana`. 20 of the 36 subdivisions had no code at
    // all, so `DL` -- 19 companies -- could not be placed and `companies.state` stayed null.
    const states = geo.areas.filter((a) => a.kind === 'state' && a.country === 'IN');
    expect(states).toHaveLength(36);
    for (const state of states) {
      const codes = state.aliases.filter((a) => /^[A-Z]{2}$/.test(a));
      expect(codes.length, `${state.name} has no ISO 3166-2 code`).toBeGreaterThan(0);
    }
  });

  it('no two subdivisions claim the same code', () => {
    // A duplicate would mean one of them silently wins every lookup, and the loser's companies
    // would be filed under the wrong state with nothing in the data to show it.
    const owner = new Map<string, string>();
    for (const state of geo.areas.filter((a) => a.kind === 'state' && a.country === 'IN')) {
      for (const code of state.aliases.filter((a) => /^[A-Z]{2}$/.test(a))) {
        expect(owner.has(code), `${code} claimed by ${owner.get(code)} and ${state.slug}`).toBe(
          false,
        );
        owner.set(code, state.slug);
      }
    }
    // 36 current codes plus the seven superseded by the 2020 merger and the 2023 revision
    // (CT, DN, DD, OR, TG, UT, UL), which older releases still carry.
    expect(owner.size).toBe(43);
  });
});
