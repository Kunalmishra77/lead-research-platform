/**
 * Geography seed (Phase 2 task 2.1): India, states/UTs and ~150 cities with bounding boxes for
 * Places search tiling. Built once by scripts/build-geo-seed.ts from OpenStreetMap Nominatim
 * (data (c) OpenStreetMap contributors, ODbL 1.0) and committed as seeds/data/geo-in.json.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export interface GeoSeed {
  kind: 'country' | 'state' | 'city';
  country: string;
  slug: string;
  name: string;
  /** Slug of the parent area (a state, or `india` for states); null for the country. */
  parent: string | null;
  aliases: string[];
  bbox: [minLat: number, minLng: number, maxLat: number, maxLng: number];
  bboxSource: 'boundary' | 'radius';
  population: number | null;
  osm: string;
}

interface GeoFile {
  source: string;
  license: string;
  generated: string;
  areas: GeoSeed[];
}

export function loadGeoSeeds(): GeoFile {
  const file = resolve(import.meta.dirname, 'data', 'geo-in.json');
  return JSON.parse(readFileSync(file, 'utf8')) as GeoFile;
}

/** The kind of an area's parent: states hang off the country, cities off a state. */
export function parentKind(kind: GeoSeed['kind']): GeoSeed['kind'] | null {
  if (kind === 'city') return 'state';
  if (kind === 'state') return 'country';
  return null;
}
