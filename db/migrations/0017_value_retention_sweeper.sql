-- Value retention (task 2.8a, ADR-0011).
--
-- Some sources licence their content to us for a limited time: Google Places content may be kept
-- for 30 days and must then be deleted. That is a different thing from `default_ttl_days`, which
-- says when a value is stale enough to re-check. Deleting on freshness would throw away a
-- customer's own CSV import after a year and our own crawl of a company's website after a month,
-- neither of which anyone asked us to delete.
--
-- So retention is explicit and opt-in: `retention_days IS NULL` means "ours to keep".

ALTER TABLE "app"."sources" ADD COLUMN "retention_days" integer;

COMMENT ON COLUMN app.sources.retention_days IS
  'Days after observed_at that values from this source must be deleted, where the provider''s '
  'terms require it. NULL = no deletion obligation. Not the same as default_ttl_days (freshness).';

ALTER TABLE app.sources
  ADD CONSTRAINT sources_retention_positive CHECK (retention_days IS NULL OR retention_days > 0);

-- Finding expired rows means reading observed_at per source; without this the sweep is a scan of
-- every partition. Partial index: only rows old enough to be interesting are ever looked at.
CREATE INDEX field_values_retention_idx ON app.field_values (source_id, observed_at);

-- ---------------------------------------------------------------- the sweeper
-- Deletes in bounded batches so a nightly run never holds a long lock on a partition. Returns
-- how many rows went, so the caller can keep calling until it returns 0.
CREATE FUNCTION app.sweep_expired_field_values(p_limit integer DEFAULT 5000)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_deleted integer;
BEGIN
  IF p_limit IS NULL OR p_limit <= 0 THEN
    RAISE EXCEPTION 'p_limit must be positive';
  END IF;

  WITH expired AS (
    SELECT fv.id, fv.observed_at
    FROM app.field_values fv
    JOIN app.sources s ON s.id = fv.source_id
    WHERE s.retention_days IS NOT NULL
      AND fv.observed_at < now() - make_interval(days => s.retention_days)
    LIMIT p_limit
  )
  DELETE FROM app.field_values fv
  USING expired e
  WHERE fv.id = e.id AND fv.observed_at = e.observed_at;

  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted;
END;
$$;

COMMENT ON FUNCTION app.sweep_expired_field_values(integer) IS
  'Deletes field_values past their source''s retention_days (ADR-0011). Batched; returns the '
  'row count so a caller can loop until it returns 0.';

-- Maintenance only: the app roles must never be able to delete values wholesale (ADR-0003).
REVOKE ALL ON FUNCTION app.sweep_expired_field_values(integer) FROM PUBLIC;

-- ---------------------------------------------------------------- schedule
-- Hourly rather than daily: the obligation is measured in days, but a job that only runs at
-- 03:00 turns a 30-day limit into "30 days plus however long since the last run".
DO $cron$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'app-sweep-expired-field-values') THEN
    PERFORM cron.unschedule('app-sweep-expired-field-values');
  END IF;
  PERFORM cron.schedule(
    'app-sweep-expired-field-values',
    '23 * * * *',
    $job$SELECT app.sweep_expired_field_values(5000);$job$
  );
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unavailable (%); schedule app.sweep_expired_field_values externally', SQLERRM;
END
$cron$;
