-- Phase 3 task 3.6: the crawler's raw responses (docs/04, docs/06 §4.9, ADR-0019).
--
-- `field_values.raw_document_id` has been a nullable column pointing at nothing since migration
-- 0013. This is the table it points at.
--
-- The row lives here and the body lives on the worker host's own disk (ADR-0019). Measured
-- before deciding: a page is 30 KB gzipped at the median, a crawl fetches 0.58 pages, and 38% of
-- leads have a site at all -- so a lakh of leads is about 660 MB of bodies against the 1 GB
-- Supabase Storage free plan, while the worker host has 134 GB free. ADR-0002 predicted exactly
-- this ("not for Phase 3 crawling; plan paid tiers") and the answer is the same one ADR-0016
-- gave for discovery: a file on a disk.
--
-- The row is written whether or not the body is. `url_hash`, `content_hash`, `http_status`,
-- `bytes`, `access_status` and `fetched_at` together say "this URL answered with exactly these
-- bytes at this time", and a content hash proves a page has not changed since without keeping a
-- copy of it.

-- ---------------------------------------------------------------- how a fetch ended
-- `ok` is a body we have. `restricted` is a door we were told not to open -- a login, a paywall,
-- a bot challenge -- and is an outcome, not a failure (docs/08, CLAUDE.md: we stop, never evade).
-- `error` is everything else: a timeout, a dead host, a 5xx.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                  WHERE t.typname = 'access_status' AND n.nspname = 'app') THEN
    CREATE TYPE app.access_status AS ENUM ('ok', 'restricted', 'error');
  END IF;
END;
$$;

-- ---------------------------------------------------------------- the documents
CREATE TABLE IF NOT EXISTS app.raw_documents (
  id uuid NOT NULL,
  url text NOT NULL,
  -- sha256 of the URL as fetched. Hashed because the index has to be small and because a URL can
  -- exceed what btree will take; the URL itself is kept beside it so a row is readable.
  url_hash bytea NOT NULL,
  source_id uuid NOT NULL,
  -- When the fetch happened. Also the partition key, so a month of crawling is one partition to
  -- detach when it ages out rather than a delete over the whole table.
  fetched_at timestamptz NOT NULL,
  http_status integer,
  content_type text,
  -- sha256 of the body. Null when there is no body: a restricted door and a dead host both have
  -- an outcome worth recording and nothing to hash.
  content_hash bytea,
  -- Where the body is, under the key docs/06 §4.9 defines. Null when nothing was stored, which
  -- is every `restricted` and `error` row and every body we already had.
  storage_key text,
  bytes integer,
  access_status app.access_status NOT NULL,
  CONSTRAINT raw_documents_pkey PRIMARY KEY (id, fetched_at),
  CONSTRAINT raw_documents_source_fk FOREIGN KEY (source_id) REFERENCES app.sources (id),
  CONSTRAINT raw_documents_url_length CHECK (length(url) <= 2048),
  CONSTRAINT raw_documents_url_hash_sha256 CHECK (length(url_hash) = 32),
  CONSTRAINT raw_documents_content_hash_sha256 CHECK (content_hash IS NULL OR length(content_hash) = 32),
  CONSTRAINT raw_documents_bytes_nonneg CHECK (bytes IS NULL OR bytes >= 0),
  -- A stored body has to be identifiable and measurable, or the retention sweeper cannot find it
  -- and nobody can tell whether it is still there.
  CONSTRAINT raw_documents_stored_body_is_described CHECK (
    storage_key IS NULL OR (content_hash IS NOT NULL AND bytes IS NOT NULL)
  ),
  -- Only a fetch that succeeded can have a body. A `restricted` row carrying one would mean we
  -- kept something we were told not to read.
  CONSTRAINT raw_documents_only_ok_has_a_body CHECK (
    access_status = 'ok' OR storage_key IS NULL
  )
) PARTITION BY RANGE (fetched_at);

-- The two questions asked of this table: "what is at this URL now" and "has it changed".
CREATE INDEX IF NOT EXISTS raw_documents_url_recent_idx
  ON app.raw_documents (url_hash, fetched_at DESC);

SELECT app.ensure_monthly_partitions('app.raw_documents', 12);

-- ---------------------------------------------------------------- the cross-partition key
-- docs/04 asks for unique(url_hash, content_hash), which a table partitioned by `fetched_at`
-- cannot enforce: every unique constraint on a partitioned table must contain the partition key,
-- so the same body fetched in two months would be two rows. ADR-0003 settled the pattern -- the
-- uniqueness lives in a small non-partitioned table -- and it is also the only thing
-- `field_values.raw_document_id` can point at, for the same reason.
--
-- This is what makes content-hash dedupe one indexed lookup: a body we already have is found
-- here, and the crawler writes no object and reuses the key.
CREATE TABLE IF NOT EXISTS app.raw_document_keys (
  raw_document_id uuid PRIMARY KEY,
  url_hash bytea NOT NULL,
  content_hash bytea NOT NULL,
  fetched_at timestamptz NOT NULL,
  storage_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT raw_document_keys_body_unique UNIQUE (url_hash, content_hash),
  CONSTRAINT raw_document_keys_url_hash_sha256 CHECK (length(url_hash) = 32),
  CONSTRAINT raw_document_keys_content_hash_sha256 CHECK (length(content_hash) = 32)
);

-- ---------------------------------------------------------------- the pointer from a value
-- ON DELETE SET NULL, deliberately. Retention prunes bodies at 90 days and rows at 12 months
-- (docs/04), and a value that outlives its page keeps its `source_url` and `observed_at` and
-- loses only the copy. Cascading would delete the evidence because its cache expired.
ALTER TABLE app.field_values
  DROP CONSTRAINT IF EXISTS field_values_raw_document_fk;
ALTER TABLE app.field_values
  ADD CONSTRAINT field_values_raw_document_fk
  FOREIGN KEY (raw_document_id) REFERENCES app.raw_document_keys (raw_document_id)
  ON DELETE SET NULL;

-- ---------------------------------------------------------------- security
ALTER TABLE app.raw_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.raw_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE app.raw_document_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.raw_document_keys FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS raw_documents_read ON app.raw_documents;
CREATE POLICY raw_documents_read ON app.raw_documents
  FOR SELECT TO app_api, app_worker USING (true);
DROP POLICY IF EXISTS raw_documents_worker_write ON app.raw_documents;
CREATE POLICY raw_documents_worker_write ON app.raw_documents
  FOR ALL TO app_worker USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS raw_document_keys_read ON app.raw_document_keys;
CREATE POLICY raw_document_keys_read ON app.raw_document_keys
  FOR SELECT TO app_api, app_worker USING (true);
DROP POLICY IF EXISTS raw_document_keys_worker_write ON app.raw_document_keys;
CREATE POLICY raw_document_keys_worker_write ON app.raw_document_keys
  FOR ALL TO app_worker USING (true) WITH CHECK (true);

-- Append-only for the worker, like `field_values`: it may add a document and may never rewrite
-- one. Deleting is the retention sweeper's job and it runs as the owner.
REVOKE ALL ON app.raw_documents, app.raw_document_keys FROM app_api, app_worker;
GRANT SELECT ON app.raw_documents, app.raw_document_keys TO app_api;
GRANT SELECT, INSERT ON app.raw_documents, app.raw_document_keys TO app_worker;

-- ---------------------------------------------------------------- partition maintenance
-- The nightly job, with raw_documents added. Rewritten whole rather than appended to, because
-- pg_cron holds one command string per job and a second schedule call would replace it anyway.
DO $cron$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'app-ensure-monthly-partitions') THEN
    PERFORM cron.unschedule('app-ensure-monthly-partitions');
  END IF;
  PERFORM cron.schedule(
    'app-ensure-monthly-partitions',
    '17 3 * * *',
    $job$SELECT app.ensure_monthly_partitions('app.audit_logs', 3); SELECT app.ensure_monthly_partitions('app.usage_events', 3); SELECT app.ensure_monthly_partitions('app.field_values', 3); SELECT app.ensure_monthly_partitions('app.raw_documents', 3);$job$
  );
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unavailable (%); schedule app.ensure_monthly_partitions externally', SQLERRM;
END
$cron$;

-- ---------------------------------------------------------------- retention
-- docs/04: bodies for 90 days, row metadata for 12 months. Only the rows are dropped here -- the
-- files are on the worker host's disk and only a worker can unlink them, so the sweeper there
-- reads `storage_key` for rows this has not yet removed.
--
-- Bounded batches, like the other sweepers (0017, 0027), so a nightly run never holds a long
-- lock on a partition.
CREATE OR REPLACE FUNCTION app.sweep_raw_documents(
  p_months integer DEFAULT 12,
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
  WITH doomed AS (
    SELECT id, fetched_at
      FROM app.raw_documents
     WHERE fetched_at < now() - make_interval(months => p_months)
     LIMIT p_limit
  )
  DELETE FROM app.raw_documents d
   USING doomed
   WHERE d.id = doomed.id AND d.fetched_at = doomed.fetched_at;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;

  -- The key rows follow their documents. `field_values.raw_document_id` is ON DELETE SET NULL,
  -- so a value older than its page keeps its `source_url` and simply stops pointing at a copy.
  DELETE FROM app.raw_document_keys k
   WHERE k.fetched_at < now() - make_interval(months => p_months);

  RETURN v_deleted;
END;
$$;

COMMENT ON FUNCTION app.sweep_raw_documents(integer, integer) IS
  'Deletes raw_documents rows older than p_months and the key rows that pointed at them. '
  'The bodies on disk are the worker sweeper''s job (ADR-0019). docs/04 retention: bodies 90 '
  'days, metadata 12 months.';

REVOKE ALL ON FUNCTION app.sweep_raw_documents(integer, integer) FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('sweep-raw-documents')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'sweep-raw-documents');
    PERFORM cron.schedule(
      'sweep-raw-documents',
      '50 3 * * *',
      $cron$SELECT app.sweep_raw_documents();$cron$
    );
  END IF;
END;
$$;
