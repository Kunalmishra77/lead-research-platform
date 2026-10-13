-- Phase 3 task 3.16: crawl metrics (docs/06 §4, the same shape as app.admin_connector_health).
--
-- Every number measured during Phase 3 was measured by hand, in a throwaway script, against the
-- live database -- the duplicate rate, the phone coverage, the 51% of domains with no MX, the
-- raw store that was keeping nothing. Each of those answers took a script somebody had to write
-- and nobody could repeat. This is those queries, once, where the admin page can read them.
--
-- SQL over rows the pipeline already writes, rather than a metrics backend: there is no Grafana
-- and no budget for one (ADR-0002), and `research_tasks`, `raw_documents` and `usage_events`
-- already carry everything 3.16 asks for. A counter in a process would also be lost on every
-- deploy, and this deployment redeploys several times a day.
--
-- Gated and audited like the other admin functions: platform staff only, one audit row per read.

-- Dropped first: the return type is part of the signature, so CREATE OR REPLACE cannot change
-- the column list of a function that already exists.
DROP FUNCTION IF EXISTS app.admin_crawl_health(uuid, timestamptz, inet, text);

CREATE FUNCTION app.admin_crawl_health(
  p_audit_id uuid,
  p_since timestamptz DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
RETURNS TABLE (
  -- the window this describes
  since timestamptz,
  -- crawl tasks
  tasks bigint,
  completed bigint,
  failed bigint,
  running bigint,
  -- what the tasks did. `pages_read` is the crawler's own count from the task output; the
  -- `raw_documents` columns below are what the store saw, and the two disagreeing is itself
  -- worth seeing -- it means pages were read and not recorded.
  pages_read bigint,
  tasks_with_no_page bigint,
  tasks_that_found_nothing bigint,
  values_written bigint,
  -- what the fetches ended as (ADR-0019: a refusal is an outcome, not a failure)
  fetches bigint,
  fetched_ok bigint,
  restricted bigint,
  errored bigint,
  -- what the raw store kept. `deduplicated` is a page we had already: docs/06 §4.9 skips
  -- storing an unchanged body, so a high number here is the dedupe working rather than waste.
  bodies_stored bigint,
  deduplicated bigint,
  bytes_stored bigint,
  -- how long a crawl takes, end to end
  avg_ms bigint,
  p95_ms bigint,
  -- The money question, and named for what it actually measures. The crawl itself is free --
  -- it calls no paid API -- so a "crawl cost" column would always read zero and teach a reader
  -- that crawling is the cheap part by accident rather than by measurement. This is every
  -- metered call in the window over every lead delivered in it, which is what "cost per
  -- candidate" is worth knowing as: a task that found a business somebody already had cost the
  -- same and delivered nothing new (ADR-0015).
  pipeline_cost_micros bigint,
  leads_delivered bigint,
  pipeline_micros_per_lead bigint,
  last_crawl_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
  v_since timestamptz := coalesce(p_since, pg_catalog.now() - interval '7 days');
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_crawl_health: platform staff only'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.crawl.read', p_ip, left(p_user_agent, 512),
            jsonb_build_object('since', v_since));

  RETURN QUERY
  WITH task AS (
    SELECT t.status,
           t.cost_micros,
           t.started_at,
           t.finished_at,
           coalesce((t.output ->> 'pages_read')::int, 0) AS pages,
           coalesce((t.output ->> 'values')::int, 0) AS values_out
      FROM app.research_tasks t
     WHERE t.type = 'crawl.company_site' AND t.created_at >= v_since
  ),
  document AS (
    -- Only the crawler's own documents. `source_id` rather than a date range on its own: a
    -- future connector keeping raw responses would otherwise be counted as crawling.
    SELECT d.access_status, d.storage_key, d.bytes, d.content_hash, d.fetched_at
      FROM app.raw_documents d
      JOIN app.sources s ON s.id = d.source_id
     WHERE s.key = 'website' AND d.fetched_at >= v_since
  ),
  spend AS (
    SELECT coalesce(sum(u.credits), 0)::bigint AS credits,
           coalesce(sum(u.cost_micros), 0)::bigint AS micros
      FROM app.usage_events u
     WHERE u.created_at >= v_since
  ),
  delivered AS (
    SELECT count(*)::bigint AS leads
      FROM app.leads l
     WHERE l.created_at >= v_since
  )
  SELECT v_since,
         (SELECT count(*) FROM task),
         (SELECT count(*) FILTER (WHERE status = 'completed') FROM task),
         (SELECT count(*) FILTER (WHERE status = 'failed') FROM task),
         (SELECT count(*) FILTER (WHERE status = 'running') FROM task),
         (SELECT coalesce(sum(pages), 0)::bigint FROM task),
         -- A crawl that read nothing: robots said no, the host was dead, or the site turned us
         -- away. 348 of 607 on 2026-10-13, which is the number that explains the email rate.
         (SELECT count(*) FILTER (WHERE pages = 0 AND status = 'completed') FROM task),
         (SELECT count(*) FILTER (WHERE pages > 0 AND values_out = 0) FROM task),
         (SELECT coalesce(sum(values_out), 0)::bigint FROM task),
         (SELECT count(*) FROM document),
         (SELECT count(*) FILTER (WHERE access_status = 'ok') FROM document),
         (SELECT count(*) FILTER (WHERE access_status = 'restricted') FROM document),
         (SELECT count(*) FILTER (WHERE access_status = 'error') FROM document),
         (SELECT count(*) FILTER (WHERE storage_key IS NOT NULL) FROM document),
         -- A body we already had: recorded, not stored again.
         (SELECT count(*) FILTER (WHERE content_hash IS NOT NULL AND storage_key IS NULL)
            FROM document),
         (SELECT coalesce(sum(bytes) FILTER (WHERE storage_key IS NOT NULL), 0)::bigint
            FROM document),
         (SELECT (avg(extract(epoch FROM (finished_at - started_at)) * 1000)
                   FILTER (WHERE started_at IS NOT NULL AND finished_at IS NOT NULL))::bigint
            FROM task),
         -- p95 as well as the mean, because one site that takes a minute moves the mean and is
         -- the thing worth finding.
         (SELECT (percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY extract(epoch FROM (finished_at - started_at)) * 1000
                  ) FILTER (WHERE started_at IS NOT NULL AND finished_at IS NOT NULL))::bigint
            FROM task),
         (SELECT micros FROM spend),
         (SELECT leads FROM delivered),
         -- Null rather than zero when nothing was delivered: dividing by no leads is not a cost
         -- of zero, it is a question with no answer, and a dashboard showing 0 would read as free.
         (SELECT CASE WHEN d.leads > 0 THEN (s.micros / d.leads)::bigint END
            FROM spend s, delivered d),
         -- From the tasks, not the documents: "when did we last crawl" is answered by the
         -- crawl, and a worker deployed without a raw store still crawls (ADR-0019).
         (SELECT max(finished_at) FROM task);
END;
$$;

COMMENT ON FUNCTION app.admin_crawl_health(uuid, timestamptz, inet, text) IS
  'Crawl metrics for a window (task 3.16): success ratio, refusals, what the raw store kept, '
  'durations and cost per delivered lead. SQL over rows the pipeline already writes -- there is '
  'no metrics backend and a counter in a process would be lost on every deploy.';

REVOKE ALL ON FUNCTION app.admin_crawl_health(uuid, timestamptz, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.admin_crawl_health(uuid, timestamptz, inet, text) TO app_api;
