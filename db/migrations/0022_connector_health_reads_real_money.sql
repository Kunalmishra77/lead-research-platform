-- 0021 gave admin_connector_health a cost_micros column read from app.research_tasks. A smoke run
-- against real data returned 45 Places tasks and cost 0 for every one of them: the discovery
-- handler calls mark_completed and mark_failed with cost_micros hardcoded to 0, because what a
-- search actually cost is known inside ConnectorHttpClient and never travels back to the handler.
--
-- So that column could only ever show zero. A page reporting 0 next to 45 real paid calls is worse
-- than a page that does not report cost at all, so the column goes and the money comes from
-- app.usage_events, which is where it really is: api_google_places, 42 calls, 1_470_000 micros.
--
-- The two cannot be one query. A task's type is the connector that ran it
-- (`discovery.places_text_search`) while a usage row's meter is what was billed
-- (`api_google_places`), and the mapping between them lives in the worker, not here. Joining them
-- on a guess would invent a number. Two reads, each from its own source of truth.

DROP FUNCTION app.admin_connector_health(uuid, timestamptz, inet, text);

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
  avg_ms bigint, last_run_at timestamptz
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
  -- Read from task outcomes rather than from a counter a connector keeps itself: a connector that
  -- dies before writing its own metric would look healthy. The error classes are the CLAUDE.md
  -- taxonomy, one column each rather than a jsonb blob, because the difference between
  -- `rate_limited` and `access_restricted` is the point of the page: one means slow down, the
  -- other means stop and never retry.
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
           (avg(extract(epoch FROM (t.finished_at - t.started_at)) * 1000)
             FILTER (WHERE t.started_at IS NOT NULL AND t.finished_at IS NOT NULL))::bigint,
           max(t.finished_at)
    FROM app.research_tasks t
    WHERE t.created_at >= v_since
    GROUP BY t.type
    ORDER BY count(*) DESC;
END;
$$;

-- What was actually billed, by meter. app.usage_events is the billing record (every paid API, LLM
-- and browser call goes through the metered client, CLAUDE.md), so this is the only place a cost
-- figure can be taken from honestly. It is partitioned monthly, hence the mandatory time window.
CREATE FUNCTION app.admin_usage_by_meter(
  p_audit_id uuid,
  p_since timestamptz DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
RETURNS TABLE (
  meter text, events bigint, units bigint, credits bigint, cost_micros bigint,
  orgs bigint, last_event_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
  v_since timestamptz := coalesce(p_since, pg_catalog.now() - interval '7 days');
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_usage_by_meter: platform staff only'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.usage.read', p_ip, left(p_user_agent, 512),
            jsonb_build_object('since', v_since));
  RETURN QUERY
    SELECT u.meter,
           count(*),
           coalesce(sum(u.units), 0)::bigint,
           coalesce(sum(u.credits), 0)::bigint,
           coalesce(sum(u.cost_micros), 0)::bigint,
           count(DISTINCT u.org_id),
           max(u.created_at)
    FROM app.usage_events u
    WHERE u.created_at >= v_since
    GROUP BY u.meter
    ORDER BY coalesce(sum(u.cost_micros), 0) DESC, count(*) DESC;
END;
$$;

REVOKE ALL ON FUNCTION app.admin_connector_health(uuid, timestamptz, inet, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_usage_by_meter(uuid, timestamptz, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.admin_connector_health(uuid, timestamptz, inet, text) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_usage_by_meter(uuid, timestamptz, inet, text) TO app_api;
