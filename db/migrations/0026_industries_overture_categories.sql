-- Overture Places categories per industry (ADR-0017).
--
-- Discovery reads a bulk dataset rather than a maps API, and Overture names things differently
-- from Google: there is no `car_repair`, no `university`, no `physiotherapist` and no
-- `bicycle_store`. Measured on Delhi, passing `google_types` to Overture reached 12.1% of its
-- 269,309 businesses; with this mapping, 96.2%.
--
-- The translation lives with the taxonomy, beside `google_types`, because it is a property of
-- what a category *means* and not of whichever connector happens to need it.

alter table app.industries
  add column if not exists overture_categories text[] not null default '{}'::text[];

comment on column app.industries.overture_categories is
  'Overture Places categories this industry means, matched against every level of a place''s '
  'category hierarchy. Empty means Overture has no equivalent and discovery falls back to '
  'searching names; it is an answer, not a gap. Seeded from db/seeds/data/overture-category-map.json.';
