-- Monthly range-partitioned, append-only tables (ADR-0003). Keep in sync with db/schema/partitioned.ts.

CREATE TABLE app.audit_logs (
  id uuid NOT NULL,
  org_id uuid,
  actor_user_id uuid,
  actor_api_key_id uuid,
  action text NOT NULL,
  target_type text,
  target_id text,
  ip inet,
  user_agent text,
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT audit_logs_pkey PRIMARY KEY (id, created_at),
  CONSTRAINT audit_logs_action_format CHECK (action ~ '^[a-z][a-z0-9_.]{2,63}$'),
  -- Audit rows outlive a deleted org (retention), but become invisible to tenants.
  CONSTRAINT audit_logs_org_fk FOREIGN KEY (org_id) REFERENCES app.organizations (id) ON DELETE SET NULL
) PARTITION BY RANGE (created_at);

CREATE INDEX audit_logs_org_created_idx ON app.audit_logs (org_id, created_at DESC);

CREATE TABLE app.usage_events (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  user_id uuid,
  research_job_id uuid,
  meter text NOT NULL,
  units bigint NOT NULL,
  credits bigint NOT NULL DEFAULT 0,
  cost_micros bigint NOT NULL DEFAULT 0,
  unit_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT usage_events_pkey PRIMARY KEY (id, created_at),
  CONSTRAINT usage_events_nonnegative CHECK (units >= 0 AND credits >= 0 AND cost_micros >= 0),
  CONSTRAINT usage_events_org_fk FOREIGN KEY (org_id) REFERENCES app.organizations (id) ON DELETE CASCADE
) PARTITION BY RANGE (created_at);

CREATE INDEX usage_events_org_created_idx ON app.usage_events (org_id, created_at DESC);
CREATE INDEX usage_events_research_job_idx ON app.usage_events (research_job_id);

-- Creates missing monthly partitions from the current month up to p_months_ahead, plus a default
-- partition. App roles get NO privileges on partition children: RLS policies of the parent are not
-- applied when a child is queried directly, so all access must go through the parent.
CREATE FUNCTION app.ensure_monthly_partitions(p_parent regclass, p_months_ahead integer DEFAULT 3)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_schema text;
  v_table text;
  v_month date := date_trunc('month', now())::date;
  v_name text;
  v_created integer := 0;
BEGIN
  IF p_months_ahead < 0 OR p_months_ahead > 24 THEN
    RAISE EXCEPTION 'p_months_ahead must be between 0 and 24' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT n.nspname, c.relname INTO v_schema, v_table
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.oid = p_parent AND c.relkind = 'p';
  IF v_table IS NULL THEN
    RAISE EXCEPTION '% is not a partitioned table', p_parent USING ERRCODE = 'invalid_parameter_value';
  END IF;

  FOR i IN 0..p_months_ahead LOOP
    v_name := v_table || '_p' || to_char(v_month + make_interval(months => i), 'YYYYMM');
    IF to_regclass(format('%I.%I', v_schema, v_name)) IS NULL THEN
      EXECUTE format(
        'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
        v_schema, v_name, v_schema, v_table,
        v_month + make_interval(months => i), v_month + make_interval(months => i + 1)
      );
      EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC, app_api, app_worker', v_schema, v_name);
      v_created := v_created + 1;
    END IF;
  END LOOP;

  v_name := v_table || '_default';
  IF to_regclass(format('%I.%I', v_schema, v_name)) IS NULL THEN
    EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I DEFAULT', v_schema, v_name, v_schema, v_table);
    EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC, app_api, app_worker', v_schema, v_name);
    v_created := v_created + 1;
  END IF;
  RETURN v_created;
END;
$$;

REVOKE ALL ON FUNCTION app.ensure_monthly_partitions(regclass, integer) FROM PUBLIC;

SELECT app.ensure_monthly_partitions('app.audit_logs', 12);
SELECT app.ensure_monthly_partitions('app.usage_events', 12);

-- Keep partitions 3 months ahead nightly via pg_cron when available (Supabase and the Supabase CLI
-- stack ship it). Without pg_cron, run the SELECTs above from a scheduled job instead.
DO $cron$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_cron;
  PERFORM cron.schedule(
    'app-ensure-monthly-partitions',
    '17 3 * * *',
    $job$SELECT app.ensure_monthly_partitions('app.audit_logs', 3); SELECT app.ensure_monthly_partitions('app.usage_events', 3);$job$
  );
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unavailable (%); schedule app.ensure_monthly_partitions externally', SQLERRM;
END
$cron$;
