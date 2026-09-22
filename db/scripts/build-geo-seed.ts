/**
 * One-off builder for seeds/data/geo-in.json (Phase 2 task 2.1): looks up bounding boxes for
 * Indian states/UTs and cities from OpenStreetMap Nominatim and writes a committed JSON file.
 * Not run by tests or seeds. Usage (from db/): node scripts/build-geo-seed.ts
 *
 * Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/): max 1 request
 * per second, identifying User-Agent, no bulk/heavy use, attribution. This script makes ~170
 * sequential requests once. Data (c) OpenStreetMap contributors, ODbL 1.0.
 */
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const USER_AGENT =
  'LeadForgeSeedBuilder/0.1 (+https://github.com/Kunalmishra77/lead-research-platform)';

/** [state/UT name, kind, cities (name or [name, ...aliases])]. Names as OSM spells them. */
type CityEntry = string | [name: string, ...aliases: string[]];
const STATES: [string, 'state' | 'ut', CityEntry[], string[]?][] = [
  [
    'Andhra Pradesh',
    'state',
    [
      'Visakhapatnam',
      'Vijayawada',
      'Guntur',
      'Nellore',
      'Tirupati',
      'Kurnool',
      'Rajahmundry',
      'Kakinada',
      'Amaravati',
    ],
    ['AP'],
  ],
  ['Arunachal Pradesh', 'state', ['Itanagar']],
  ['Assam', 'state', [['Guwahati', 'Gauhati'], 'Dibrugarh', 'Silchar', 'Jorhat'], []],
  ['Bihar', 'state', ['Patna', 'Gaya', 'Bhagalpur', 'Muzaffarpur', 'Darbhanga'], []],
  ['Chhattisgarh', 'state', ['Raipur', 'Bhilai', 'Bilaspur', 'Korba'], ['CG']],
  ['Goa', 'state', [['Panaji', 'Panjim'], 'Margao', 'Vasco da Gama'], []],
  [
    'Gujarat',
    'state',
    [
      'Ahmedabad',
      'Surat',
      ['Vadodara', 'Baroda'],
      'Rajkot',
      'Bhavnagar',
      'Jamnagar',
      'Gandhinagar',
      'Junagadh',
      'Anand',
    ],
    ['GJ'],
  ],
  [
    'Haryana',
    'state',
    [
      ['Gurugram', 'Gurgaon'],
      'Faridabad',
      'Panipat',
      'Ambala',
      'Karnal',
      'Hisar',
      'Rohtak',
      'Sonipat',
    ],
    ['HR'],
  ],
  ['Himachal Pradesh', 'state', ['Shimla', 'Dharamshala', 'Manali', 'Solan'], ['HP']],
  ['Jharkhand', 'state', ['Ranchi', 'Jamshedpur', 'Dhanbad', ['Bokaro', 'Bokaro Steel City']], []],
  [
    'Karnataka',
    'state',
    [
      ['Bengaluru', 'Bangalore'],
      ['Mysuru', 'Mysore'],
      ['Mangaluru', 'Mangalore'],
      'Hubballi',
      ['Belagavi', 'Belgaum'],
      ['Kalaburagi', 'Gulbarga'],
      'Davanagere',
      'Udupi',
    ],
    ['KA'],
  ],
  [
    'Kerala',
    'state',
    [
      ['Thiruvananthapuram', 'Trivandrum'],
      ['Kochi', 'Cochin', 'Ernakulam'],
      ['Kozhikode', 'Calicut'],
      ['Thrissur', 'Trichur'],
      'Kollam',
      'Kannur',
    ],
    ['KL'],
  ],
  [
    'Madhya Pradesh',
    'state',
    ['Indore', 'Bhopal', 'Jabalpur', 'Gwalior', 'Ujjain', 'Sagar'],
    ['MP'],
  ],
  [
    'Maharashtra',
    'state',
    [
      ['Mumbai', 'Bombay'],
      'Pune',
      'Nagpur',
      'Nashik',
      'Thane',
      ['Chhatrapati Sambhajinagar', 'Aurangabad'],
      'Solapur',
      'Kolhapur',
      'Navi Mumbai',
      'Pimpri-Chinchwad',
      'Amravati',
      'Nanded',
    ],
    ['MH'],
  ],
  ['Manipur', 'state', ['Imphal']],
  ['Meghalaya', 'state', ['Shillong']],
  ['Mizoram', 'state', ['Aizawl']],
  ['Nagaland', 'state', ['Kohima', 'Dimapur']],
  ['Odisha', 'state', ['Bhubaneswar', 'Cuttack', 'Rourkela', 'Puri'], ['Orissa']],
  [
    'Punjab',
    'state',
    ['Ludhiana', 'Amritsar', 'Jalandhar', 'Patiala', 'Bathinda', 'Mohali'],
    ['PB'],
  ],
  [
    'Rajasthan',
    'state',
    ['Jaipur', 'Jodhpur', 'Kota', 'Udaipur', 'Ajmer', 'Bikaner', 'Alwar', 'Bhilwara'],
    ['RJ'],
  ],
  ['Sikkim', 'state', ['Gangtok']],
  [
    'Tamil Nadu',
    'state',
    [
      ['Chennai', 'Madras'],
      'Coimbatore',
      ['Madurai'],
      ['Tiruchirappalli', 'Trichy'],
      'Salem',
      'Tiruppur',
      'Erode',
      'Vellore',
      ['Thoothukudi', 'Tuticorin'],
      'Tirunelveli',
    ],
    ['TN'],
  ],
  [
    'Telangana',
    'state',
    ['Hyderabad', 'Warangal', 'Karimnagar', 'Nizamabad', 'Secunderabad'],
    ['TS'],
  ],
  ['Tripura', 'state', ['Agartala']],
  [
    'Uttar Pradesh',
    'state',
    [
      'Lucknow',
      'Kanpur',
      'Ghaziabad',
      'Agra',
      ['Varanasi', 'Banaras', 'Kashi'],
      'Meerut',
      ['Prayagraj', 'Allahabad'],
      'Noida',
      'Bareilly',
      'Aligarh',
      'Moradabad',
      'Gorakhpur',
      'Mathura',
      'Greater Noida',
    ],
    ['UP'],
  ],
  ['Uttarakhand', 'state', ['Dehradun', 'Haridwar', 'Rishikesh', 'Haldwani', 'Nainital'], ['UK']],
  [
    'West Bengal',
    'state',
    [['Kolkata', 'Calcutta'], 'Howrah', 'Durgapur', 'Asansol', 'Siliguri'],
    ['WB'],
  ],
  ['Andaman and Nicobar Islands', 'ut', [['Sri Vijaya Puram', 'Port Blair']]],
  ['Chandigarh', 'ut', ['Chandigarh']],
  ['Dadra and Nagar Haveli and Daman and Diu', 'ut', ['Daman', 'Silvassa']],
  [
    'Delhi',
    'ut',
    [['Delhi', 'New Delhi', 'NCT Delhi', 'Dilli']],
    ['NCT of Delhi', 'National Capital Territory of Delhi'],
  ],
  ['Jammu and Kashmir', 'ut', ['Srinagar', 'Jammu'], ['J&K']],
  ['Ladakh', 'ut', ['Leh']],
  ['Lakshadweep', 'ut', ['Kavaratti']],
  ['Puducherry', 'ut', [['Puducherry', 'Pondicherry', 'Pondy']]],
];

interface NominatimResult {
  boundingbox: [string, string, string, string]; // minlat, maxlat, minlon, maxlon
  lat: string;
  lon: string;
  osm_type: 'node' | 'way' | 'relation';
  display_name: string;
  addresstype?: string;
  address?: { state?: string; 'ISO3166-2-lvl4'?: string };
  extratags?: { population?: string };
}

/** Display name -> OSM search name, where OSM only knows the city by its corporation name. */
const OSM_QUERY_NAME: Record<string, string> = { Nanded: 'Nanded-Waghala' };

/** UTs that effectively are one city: use the UT boundary as the city box. */
const CITY_IS_UT = new Set(['Delhi', 'Chandigarh']);
/** A "city" boundary wider than this (degrees) is a district in disguise: use a radius box. */
const MAX_CITY_SPAN_DEG = 0.5;

/** City-level admin boundaries; districts and states are far larger than the city itself. */
const CITY_BOUNDARY_TYPES = new Set(['city', 'town', 'municipality', 'village']);

export interface GeoSeed {
  kind: 'country' | 'state' | 'city';
  country: 'IN';
  slug: string;
  name: string;
  parent: string | null;
  aliases: string[];
  bbox: [minLat: number, minLng: number, maxLat: number, maxLng: number];
  /** `boundary`: OSM admin boundary; `radius`: a box around the city point sized by population. */
  bboxSource: 'boundary' | 'radius';
  population: number | null;
  osm: string;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function search(params: Record<string, string>, limit: number): Promise<NominatimResult[]> {
  const url = new URL('https://nominatim.openstreetmap.org/search');
  for (const [k, v] of Object.entries({
    ...params,
    format: 'jsonv2',
    limit: String(limit),
    extratags: '1',
    addressdetails: '1',
    countrycodes: 'in',
  })) {
    url.searchParams.set(k, v);
  }
  for (let attempt = 1; ; attempt++) {
    await sleep(1100); // policy: at most 1 request per second
    const res = await fetch(url, {
      headers: { 'user-agent': USER_AGENT, 'accept-language': 'en' },
    });
    if (res.ok) return (await res.json()) as NominatimResult[];
    // 429/503 = the service asks us to slow down: back off politely, give up after 4 tries.
    if ((res.status === 429 || res.status === 503) && attempt < 4) {
      console.log(`Nominatim ${String(res.status)}; backing off ${String(30 * attempt)} s`);
      await sleep(30_000 * attempt);
      continue;
    }
    throw new Error(`Nominatim ${String(res.status)} for ${url.search}`);
  }
}

async function lookup(params: Record<string, string>): Promise<NominatimResult> {
  const [hit] = await search(params, 1);
  if (!hit) throw new Error(`no result for ${JSON.stringify(params)}`);
  return hit;
}

/**
 * Prefers a city-level boundary, otherwise the city point (the caller sizes a box around it).
 * Only hits whose address is in the requested state are accepted, so a same-named town elsewhere
 * (or a road/POI) can never be picked; an unmatched city fails the build loudly.
 */
async function lookupCity(name: string, state: string): Promise<NominatimResult> {
  const hits = (await search({ q: `${name}, ${state}, India` }, 8)).filter(
    (h) => h.address?.state?.toLowerCase() === state.toLowerCase(),
  );
  const cityLevel = hits.filter((h) => CITY_BOUNDARY_TYPES.has(h.addresstype ?? ''));
  const hit = cityLevel.find((h) => h.osm_type === 'relation') ?? cityLevel[0];
  if (!hit) {
    throw new Error(`no city-level result in ${state} for ${name}: fix the name or add it by hand`);
  }
  return hit;
}

function radiusKm(population: number | null): number {
  if (population !== null && population >= 5_000_000) return 25;
  if (population !== null && population >= 1_000_000) return 15;
  return 10;
}

function radiusBox(lat: number, lng: number, km: number): GeoSeed['bbox'] {
  const dLat = km / 111.32;
  const dLng = km / (111.32 * Math.cos((lat * Math.PI) / 180));
  const r = (x: number) => Math.round(x * 1e6) / 1e6;
  return [r(lat - dLat), r(lng - dLng), r(lat + dLat), r(lng + dLng)];
}

function toSeed(
  hit: NominatimResult,
  kind: GeoSeed['kind'],
  name: string,
  parent: string | null,
  aliases: string[],
): GeoSeed {
  const pop = Number(hit.extratags?.population?.replace(/[^0-9]/g, ''));
  const population = Number.isFinite(pop) && pop > 0 ? pop : null;
  const [minLat, maxLat, minLng, maxLng] = hit.boundingbox.map(Number) as [
    number,
    number,
    number,
    number,
  ];
  const span = Math.max(maxLat - minLat, maxLng - minLng);
  const isBoundary =
    hit.osm_type !== 'node' &&
    (kind !== 'city' || CITY_IS_UT.has(name) || span <= MAX_CITY_SPAN_DEG);
  return {
    kind,
    country: 'IN',
    slug: slugify(name),
    name,
    parent,
    aliases,
    bbox: isBoundary
      ? [minLat, minLng, maxLat, maxLng]
      : radiusBox(Number(hit.lat), Number(hit.lon), radiusKm(population)),
    bboxSource: isBoundary ? 'boundary' : 'radius',
    population,
    osm: hit.display_name,
  };
}

const out: GeoSeed[] = [];
const failures: string[] = [];
const india = await lookup({ country: 'India' });
out.push(toSeed(india, 'country', 'India', null, ['Bharat']));
for (const [state, , cities, stateAliases = []] of STATES) {
  const stateHit = await lookup({ state, country: 'India' });
  out.push(toSeed(stateHit, 'state', state, 'india', stateAliases));
  for (const entry of cities) {
    const [name, ...aliases] = typeof entry === 'string' ? [entry] : entry;
    // Delhi and Chandigarh are the whole UT: use the UT boundary rather than a city point.
    let hit: NominatimResult;
    try {
      hit = CITY_IS_UT.has(name) ? stateHit : await lookupCity(OSM_QUERY_NAME[name] ?? name, state);
    } catch (err) {
      failures.push(err instanceof Error ? err.message : String(err));
      continue;
    }
    const seed = toSeed(hit, 'city', name, slugify(state), aliases);
    // State-qualified slug: several states have same-named cities (Aurangabad, Bilaspur, ...).
    seed.slug = `${slugify(name)}-${slugify(state)}`;
    out.push(seed);
    console.log(`${state} / ${name}: ${seed.bboxSource} ${seed.bbox.join(',')}`);
  }
}

if (failures.length > 0) {
  throw new Error(`unmatched cities (nothing written):\n${failures.join('\n')}`);
}

const file = resolve(import.meta.dirname, '..', 'seeds', 'data', 'geo-in.json');
writeFileSync(
  file,
  `${JSON.stringify(
    {
      source: 'OpenStreetMap Nominatim (https://nominatim.openstreetmap.org)',
      license:
        'Data (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)',
      generated: new Date().toISOString().slice(0, 10),
      areas: out,
    },
    null,
    1,
  )}\n`,
);
console.log(`wrote ${String(out.length)} areas to ${file}`);
