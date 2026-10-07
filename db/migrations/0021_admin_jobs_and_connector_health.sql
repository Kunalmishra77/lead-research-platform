-- Task 2.13: what platform staff need to answer "why did that job find nothing?" without reading
-- container logs. Three reads, shaped like the admin reads in 0011: the staff flag is checked and
-- the audit row written in the same transaction as the query, so a look at another tenant's job
-- cannot happen unrecorded.
--
-- These read tenant tables across orgs, which only a SECURITY DEFINER function may do -- app_api
-- is subject to RLS (0003) and has no cross-org grant. `app` is not on the search path, so every
-- reference is schema-qualified.

CREATE FUNCTION app.admin_list_jobs(
  p_audit_id uuid,
  p_limit integer DEFAULT 50,
  p_before uuid DEFAULT NULL,
  p_status app.research_job_status DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
RETURNS TABLE (
  id uuid, org_id uuid, org_name text, status app.research_job_status, depth app.research_depth,
  credit_budget integer, credits_reserved integer, credits_used integer, cost_micros bigint,
  progress jsonb, error_class app.error_class, created_at timestamptz, started_at timestamptz,
  finished_at timestamptz, settled_at timestamptz,
  tasks_total bigint, tasks_failed bigint
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_list_jobs: platform staff only' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.jobs.listed', p_ip, left(p_user_agent, 512),
            jsonb_build_object('limit', p_limit, 'before', p_before, 'status', p_status));
  -- Newest first, which is what anyone opening this page wants. Ids are UUID v7, so `id <` is a
  -- stable cursor by creation time and needs no second sort key.
  RETURN QUERY
    SELECT j.id, j.org_id, o.name, j.status, j.depth,
           j.credit_budget, j.credits_reserved, j.credits_used, j.cost_micros,
           j.progress, j.error_class, j.created_at, j.started_at, j.finished_at, j.settled_at,
           (SELECT count(*) FROM app.research_tasks t WHERE t.research_job_id = j.id),
           (SELECT count(*) FROM app.research_tasks t
             WHERE t.research_job_id = j.id AND t.status = 'failed')
    FROM app.research_jobs j
    JOIN app.organizations o ON o.id = j.org_id
    WHERE (p_before IS NULL OR j.id < p_before)
      AND (p_status IS NULL OR j.status = p_status)
    ORDER BY j.id DESC
    LIMIT least(greatest(p_limit, 1), 201);
END;
$$;

-- One job's tasks, with the parent link, so the caller can draw the DAG rather than a flat list.
-- A job fans out one task per search, so the cap is high enough for a city-wide run and still
-- bounded: a page that tried to draw ten thousand nodes would hang the browser, not inform anyone.
CREATE FUNCTION app.admin_job_tasks(
  p_audit_id uuid,
  p_job_id uuid,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
RETURNS TABLE (
  id uuid, parent_task_id uuid, type text, status app.research_task_status, attempts smallint,
  credit_budget integer, cost_micros bigint, error_class app.error_class,
  started_at timestamptz, finished_at timestamptz, duration_ms bigint, input jsonb
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_job_tasks: platform staff only' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.job.tasks.read', p_ip, left(p_user_agent, 512),
            jsonb_build_object('job_id', p_job_id));
  RETURN QUERY
    SELECT t.id, t.parent_task_id, t.type, t.status, t.attempts,
           t.credit_budget, t.cost_micros, t.error_class,
           t.started_at, t.finished_at,
           CASE WHEN t.started_at IS NULL OR t.finished_at IS NULL THEN NULL
                ELSE (extract(epoch FROM (t.finished_at - t.started_at)) * 1000)::bigint END,
           -- The input decides what a task searched for, which is the first thing to check when a
           -- task returned nothing. It holds a query and an area, never a credential.
           t.input
    FROM app.research_tasks t
    WHERE t.research_job_id = p_job_id
    ORDER BY t.id
    LIMIT 2001;
END;
$$;

-- Connector health, read from task outcomes rather than from a counter a connector keeps itself:
-- a connector that dies before writing its own metric would look healthy. Grouped by task type,
-- which is the connector that ran it (`discovery.places_text_search` is the Places connector).
--
-- The error classes are the taxonomy from CLAUDE.md and are listed one column each rather than as
-- a jsonb blob, because the difference between `rate_limited` and `access_restricted` is the whole
-- point of the page: one means slow down, the other means stop.
CREATE FUNCTION app.admin_connector_health(
  p_audit_id uuid,
  p_since timestamptz DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
RETURNS TABLE (
  source text, total bigint, completed bigint, failed bigint, running bigint, queued bigint,
  transient bigint, rate_limited bigint, access_restricted bigint, parse_failed bigint,
  invalid_input bigint, budget_exhausted bigint,
  cost_micros bigint, avg_ms bigint, last_run_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
  v_since timestamptz := coalesce(p_since, pg_catalog.now() - interval '7 days');
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_connector_health: platform staff only'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.connectors.read', p_ip, left(p_user_agent, 512),
            jsonb_build_object('since', v_since));
  RETURN QUERY
    SELECT t.type,
           count(*),
           count(*) FILTER (WHERE t.status = 'completed'),
           count(*) FILTER (WHERE t.status = 'failed'),
           count(*) FILTER (WHERE t.status = 'running'),
           count(*) FILTER (WHERE t.status = 'queued'),
           count(*) FILTER (WHERE t.error_class = 'transient'),
           count(*) FILTER (WHERE t.error_class = 'rate_limited'),
           count(*) FILTER (WHERE t.error_class = 'access_restricted'),
           count(*) FILTER (WHERE t.error_class = 'parse_failed'),
           count(*) FILTER (WHERE t.error_class = 'invalid_input'),
           count(*) FILTER (WHERE t.error_class = 'budget_exhausted'),
           coalesce(sum(t.cost_micros), 0)::bigint,
           (avg(extract(epoch FROM (t.finished_at - t.started_at)) * 1000)
             FILTER (WHERE t.started_at IS NOT NULL AND t.finished_at IS NOT NULL))::bigint,
           max(t.finished_at)
    FROM app.research_tasks t
    WHERE t.created_at >= v_since
    GROUP BY t.type
    ORDER BY count(*) DESC;
END;
$$;

REVOKE ALL ON FUNCTION app.admin_list_jobs(uuid, integer, uuid, app.research_job_status, inet, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_job_tasks(uuid, uuid, inet, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_connector_health(uuid, timestamptz, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.admin_list_jobs(uuid, integer, uuid, app.research_job_status, inet, text) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_job_tasks(uuid, uuid, inet, text) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_connector_health(uuid, timestamptz, inet, text) TO app_api;
