/** Idempotent seeds. Usage: pnpm db:seed (runs as the owner role). */
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';

import { loadGeoSeeds, parentKind } from './geo.ts';
import { INDUSTRY_SEEDS } from './industries.ts';
import { SOURCE_SEEDS } from './sources.ts';

const envFile = resolve(import.meta.dirname, '..', '..', '.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

const url = process.env.DATABASE_URL_MIGRATIONS;
if (!url) throw new Error('DATABASE_URL_MIGRATIONS is required (see infra/setup/SETUP.md)');

const sql = postgres(url, { max: 1, prepare: false });
try {
  await sql.begin(async (tx) => {
    for (const s of SOURCE_SEEDS) {
      // Policy flags (enabled, legal_approved) are admin decisions and never overwritten by seeds.
      await tx`
        insert into app.sources (id, key, name, type, tos_class, default_ttl_days)
        values (${uuidv7()}, ${s.key}, ${s.name}, ${s.type}, ${s.tosClass}, ${s.defaultTtlDays})
        on conflict (key) do update
          set name = excluded.name, type = excluded.type, tos_class = excluded.tos_class,
              default_ttl_days = excluded.default_ttl_days`;
    }

    // Industries: upsert by slug, then link parents by slug (parents are listed before children).
    for (const i of INDUSTRY_SEEDS) {
      await tx`
        insert into app.industries (id, slug, name, synonyms, google_types)
        values (${uuidv7()}, ${i.slug}, ${i.name}, ${i.synonyms}, ${i.googleTypes})
        on conflict (slug) do update
          set name = excluded.name, synonyms = excluded.synonyms, google_types = excluded.google_types`;
    }
    for (const i of INDUSTRY_SEEDS) {
      const linked = i.parent
        ? await tx`
            update app.industries c set parent_id = p.id
            from app.industries p
            where c.slug = ${i.slug} and p.slug = ${i.parent}`
        : await tx`update app.industries set parent_id = null where slug = ${i.slug}`;
      if (linked.count !== 1)
        throw new Error(`industry ${i.slug}: parent ${String(i.parent)} not found`);
    }

    // Geography: upsert by (country, kind, slug); parents resolved by slug and parent kind.
    const geo = loadGeoSeeds();
    for (const a of geo.areas) {
      const [minLat, minLng, maxLat, maxLng] = a.bbox;
      await tx`
        insert into app.geo_areas (id, kind, country, slug, name, aliases, min_lat, min_lng, max_lat, max_lng, population)
        values (${uuidv7()}, ${a.kind}, ${a.country}, ${a.slug}, ${a.name}, ${a.aliases},
                ${minLat}, ${minLng}, ${maxLat}, ${maxLng}, ${a.population})
        on conflict (country, kind, slug) do update
          set name = excluded.name, aliases = excluded.aliases, min_lat = excluded.min_lat,
              min_lng = excluded.min_lng, max_lat = excluded.max_lat, max_lng = excluded.max_lng,
              population = excluded.population`;
    }
    for (const a of geo.areas) {
      const kind = parentKind(a.kind);
      const linked =
        a.parent && kind
          ? await tx`
              update app.geo_areas c set parent_id = p.id
              from app.geo_areas p
              where c.country = ${a.country} and c.kind = ${a.kind} and c.slug = ${a.slug}
                and p.country = ${a.country} and p.kind = ${kind} and p.slug = ${a.parent}`
          : await tx`
              update app.geo_areas set parent_id = null
              where country = ${a.country} and kind = ${a.kind} and slug = ${a.slug}`;
      if (linked.count !== 1)
        throw new Error(`geo ${a.kind} ${a.slug}: parent ${String(a.parent)} not found`);
    }
    // The seed owns geo_areas (nothing references them yet): drop areas no longer in the file,
    // e.g. after a slug scheme change. Children first, so parent FKs never block the delete.
    const keys = geo.areas.map((a) => `${a.country}|${a.kind}|${a.slug}`);
    await tx`
      delete from app.geo_areas
      where country || '|' || kind || '|' || slug <> all(${keys}::text[])
        and kind = 'city'`;
    await tx`
      delete from app.geo_areas
      where country || '|' || kind || '|' || slug <> all(${keys}::text[])`;
  });
  const [counts] = await sql<{ sources: number; industries: number; geo: number }[]>`
    select (select count(*) from app.sources)::int as sources,
           (select count(*) from app.industries)::int as industries,
           (select count(*) from app.geo_areas)::int as geo`;
  console.log(
    `seed: ${String(counts?.sources)} sources, ${String(counts?.industries)} industries, ${String(counts?.geo)} geo areas`,
  );
} finally {
  await sql.end();
}
