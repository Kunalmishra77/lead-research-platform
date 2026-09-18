-- Hardening after the task 1.6 security review (ADR-0003 amendment).

-- 1. usage_events can only reference research jobs of its own org (NULL research_job_id is allowed).
ALTER TABLE app.usage_events
  ADD CONSTRAINT usage_events_job_org_fk FOREIGN KEY (research_job_id, org_id)
  REFERENCES app.research_jobs (id, org_id) ON DELETE CASCADE;

-- 2. Partition children: RLS enabled + forced with no policies = deny-all for app roles, even if a
--    later blanket GRANT reaches them. Access always goes through the parent (whose policies apply).
CREATE OR REPLACE FUNCTION app.ensure_monthly_partitions(p_parent regclass, p_months_ahead integer DEFAULT 3)
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

  FOR i IN 0..p_months_ahead + 1 LOOP
    IF i <= p_months_ahead THEN
      v_name := v_table || '_p' || to_char(v_month + make_interval(months => i), 'YYYYMM');
    ELSE
      v_name := v_table || '_default';
    END IF;
    IF to_regclass(format('%I.%I', v_schema, v_name)) IS NULL THEN
      IF i <= p_months_ahead THEN
        EXECUTE format(
          'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
          v_schema, v_name, v_schema, v_table,
          v_month + make_interval(months => i), v_month + make_interval(months => i + 1)
        );
      ELSE
        EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I DEFAULT', v_schema, v_name, v_schema, v_table);
      END IF;
      v_created := v_created + 1;
    END IF;
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', v_schema, v_name);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', v_schema, v_name);
    EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC, app_api, app_worker', v_schema, v_name);
  END LOOP;
  RETURN v_created;
END;
$$;

REVOKE ALL ON FUNCTION app.ensure_monthly_partitions(regclass, integer) FROM PUBLIC;

DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'app' AND c.relkind = 'r' AND c.relispartition
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.nspname, r.relname);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', r.nspname, r.relname);
    EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC, app_api, app_worker', r.nspname, r.relname);
  END LOOP;
END
$$;

-- 3. Billing columns change only through definer functions (Phase 2+). Users may rename their org.
REVOKE UPDATE ON app.organizations FROM app_api, app_worker;
GRANT UPDATE (name, slug) ON app.organizations TO app_api;
-- The API never writes ledger rows directly (reservations/grants go through definer functions).
REVOKE INSERT ON app.credit_ledger FROM app_api;

-- 4. Audit rows cannot claim another actor.
DROP POLICY audit_logs_tenant ON app.audit_logs;
CREATE POLICY audit_logs_tenant_read ON app.audit_logs FOR SELECT TO app_api, app_worker
  USING (org_id = app.current_org_id());
CREATE POLICY audit_logs_tenant_insert ON app.audit_logs FOR INSERT TO app_api, app_worker
  WITH CHECK (
    org_id = app.current_org_id()
    AND (actor_user_id IS NULL OR actor_user_id = app.current_user_id())
  );

-- 5. bootstrap_org: reject banned/deleted users; serialize per user so the org cap cannot race.
CREATE OR REPLACE FUNCTION app.bootstrap_org(
  p_user_id uuid,
  p_org_id uuid,
  p_org_name text,
  p_org_slug text,
  p_workspace_id uuid,
  p_workspace_name text,
  p_membership_id uuid,
  p_audit_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_owned integer;
BEGIN
  IF p_user_id IS NULL OR p_user_id IS DISTINCT FROM app.current_user_id() THEN
    RAISE EXCEPTION 'bootstrap_org: caller does not match app.user_id' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM auth.users u
    WHERE u.id = p_user_id
      AND u.email_confirmed_at IS NOT NULL
      AND u.deleted_at IS NULL
      AND (u.banned_until IS NULL OR u.banned_until <= now())
  ) THEN
    RAISE EXCEPTION 'bootstrap_org: user missing, unconfirmed, banned or deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('bootstrap_org:' || p_user_id::text, 0));
  SELECT count(*) INTO v_owned FROM app.memberships WHERE user_id = p_user_id AND role = 'owner';
  IF v_owned >= 10 THEN
    RAISE EXCEPTION 'bootstrap_org: organization limit reached' USING ERRCODE = 'check_violation';
  END IF;

  INSERT INTO app.organizations (id, name, slug) VALUES (p_org_id, p_org_name, p_org_slug);
  INSERT INTO app.workspaces (id, org_id, name) VALUES (p_workspace_id, p_org_id, p_workspace_name);
  INSERT INTO app.memberships (id, org_id, workspace_id, user_id, role)
    VALUES (p_membership_id, p_org_id, p_workspace_id, p_user_id, 'owner');
  INSERT INTO app.user_profiles (user_id) VALUES (p_user_id) ON CONFLICT (user_id) DO NOTHING;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, target_type, target_id, meta)
    VALUES (p_audit_id, p_org_id, p_user_id, 'org.created', 'organization', p_org_id::text,
            jsonb_build_object('workspace_id', p_workspace_id));
END;
$$;

REVOKE ALL ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid) TO app_api;
