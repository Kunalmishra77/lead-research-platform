-- Export and prune (ADR-0016): leads flow through the database, they do not pile up in it.
--
-- Measured on 2026-10-12, with 1,001 leads in the live database:
--
--   field_values      14.7 MB of 18.0 MB   82% of everything stored
--   leads              0.4 MB              400 bytes a lead
--   companies          0.3 MB
--
--   per lead          17.1 KB  (31.6 values each, at about 470 bytes)
--   database            43 MB of the free plan's 500 MB
--   room for         27,307 more leads, in total
--
-- So the ceiling is real and it is field_values. ADR-0016 proposed deleting leads and keeping a
-- lean record that one had been delivered; the measurement says that is more than is needed.
-- A lead row is 400 bytes and it is already the record of delivery -- deleting it and rebuilding
-- the same fact in another table would save a rounding error and risk delivering a business to
-- the same customer twice.
--
-- What is pruned is the values. What is kept is the lead, the company and the location: a
-- pruned lead still has its name, domain, city, address and phone, because those live in
-- `companies` and `company_locations`. What goes is everything else -- email, socials, hours,
-- rating -- which the customer already has in the CSV they exported.

-- ---------------------------------------------------------------- when a lead was handed over
ALTER TABLE app.leads ADD COLUMN IF NOT EXISTS exported_at timestamptz;

COMMENT ON COLUMN app.leads.exported_at IS
  'When this lead was last included in an export. Null means the customer has not taken it yet, '
  'and nothing of it is ever pruned: the ceiling applies to work in progress, not to delivered '
  'work somebody has not collected.';

-- The sweep asks "has every lead on this company been exported and gone cold", so it reads
-- company_id and exported_at together.
CREATE INDEX IF NOT EXISTS leads_export_sweep_idx ON app.leads (company_id, exported_at);

-- ---------------------------------------------------------------- the sweeper
-- Deletes in bounded batches, like app.sweep_expired_field_values (0017), so a nightly run never
-- holds a long lock on a partition. Returns how many rows went, so a caller can keep calling
-- until it returns 0.
CREATE OR REPLACE FUNCTION app.sweep_exported_field_values(
  p_grace_days integer DEFAULT 30,
  p_limit integer DEFAULT 5000
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app, pg_catalog
AS $$
DECLARE
  v_deleted integer;
BEGIN
  WITH done AS (
    -- A company is finished with only when *every* workspace holding it is finished with it.
    -- The graph is shared: one customer exporting a business must never blank it out for
    -- another who is still working it. `bool_and` over all of a company's leads is what says
    -- so, and `having count(*) > 0` keeps companies nobody holds out of it -- their values
    -- belong to the source retention sweeper (0017), not to this one.
    SELECT l.company_id
      FROM app.leads l
     GROUP BY l.company_id
    HAVING count(*) > 0
       AND bool_and(l.exported_at IS NOT NULL
                    AND l.exported_at < now() - make_interval(days => p_grace_days))
  ),
  doomed AS (
    SELECT v.id, v.observed_at
      FROM app.field_values v
      JOIN done ON done.company_id = v.entity_id
     WHERE v.entity_type = 'company'
     LIMIT p_limit
  )
  DELETE FROM app.field_values v
   USING doomed
   WHERE v.id = doomed.id AND v.observed_at = doomed.observed_at;

  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted;
END;
$$;

COMMENT ON FUNCTION app.sweep_exported_field_values(integer, integer) IS
  'Deletes field values of companies whose every lead was exported more than p_grace_days ago. '
  'The lead, company and location stay, so a pruned lead keeps its name, address and phone. '
  'ADR-0016: the product delivers leads rather than storing them.';

REVOKE ALL ON FUNCTION app.sweep_exported_field_values(integer, integer) FROM PUBLIC;

-- ---------------------------------------------------------------- scheduled
-- Nightly, after the retention sweep, and deliberately not in the same statement: one of them
-- failing must not stop the other, and they delete for unrelated reasons.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('sweep-exported-field-values')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'sweep-exported-field-values');
    PERFORM cron.schedule(
      'sweep-exported-field-values',
      '40 3 * * *',
      $cron$SELECT app.sweep_exported_field_values();$cron$
    );
  END IF;
END;
$$;
