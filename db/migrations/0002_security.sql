-- Tenant isolation, privileges and privileged entry points (ADR-0003, docs/04 RLS pattern).
-- Invariant (asserted by the RLS test suite): every table in schema app has RLS enabled AND forced.

-- ---------------------------------------------------------------- context helpers
-- Set per transaction by withTenant()/withUser(): set_config('app.org_id' | 'app.user_id', ..., true).
CREATE FUNCTION app.current_org_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$;

CREATE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

GRANT EXECUTE ON FUNCTION app.current_org_id(), app.current_user_id() TO app_api, app_worker;

-- ---------------------------------------------------------------- updated_at
CREATE FUNCTION app.set_updated_at() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

GRANT EXECUTE ON FUNCTION app.set_updated_at() TO app_api, app_worker;

CREATE TRIGGER organizations_updated_at BEFORE UPDATE ON app.organizations FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER workspaces_updated_at BEFORE UPDATE ON app.workspaces FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER memberships_updated_at BEFORE UPDATE ON app.memberships FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER user_profiles_updated_at BEFORE UPDATE ON app.user_profiles FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER sources_updated_at BEFORE UPDATE ON app.sources FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER research_jobs_updated_at BEFORE UPDATE ON app.research_jobs FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- ---------------------------------------------------------------- auth.users references
ALTER TABLE app.memberships
  ADD CONSTRAINT memberships_user_fk FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE;
ALTER TABLE app.user_profiles
  ADD CONSTRAINT user_profiles_user_fk FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE;

-- ---------------------------------------------------------------- privileges beyond bootstrap defaults
-- Append-only tables.
REVOKE UPDATE, DELETE, TRUNCATE ON app.credit_ledger, app.audit_logs, app.usage_events, app.usage_unit_keys
  FROM app_api, app_worker;
-- Sources are written by workers/admin tooling only.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.sources FROM app_api;
-- Organizations are created only through app.bootstrap_org and never deleted by the app.
REVOKE INSERT, DELETE, TRUNCATE ON app.organizations FROM app_api, app_worker;
-- Users may edit their own name, never the platform staff flag.
REVOKE INSERT, UPDATE, TRUNCATE ON app.user_profiles FROM app_api, app_worker;
GRANT INSERT (user_id, full_name), UPDATE (full_name) ON app.user_profiles TO app_api;
REVOKE ALL ON app.user_profiles FROM app_worker;
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA app FROM app_api, app_worker;

-- ---------------------------------------------------------------- row level security
ALTER TABLE app.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE app.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE app.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.user_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE app.sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.sources FORCE ROW LEVEL SECURITY;
ALTER TABLE app.research_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.research_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE app.credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.credit_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE app.usage_unit_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.usage_unit_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE app.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_logs FORCE ROW LEVEL SECURITY;
ALTER TABLE app.usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.usage_events FORCE ROW LEVEL SECURITY;

-- organizations: read the active org or any org you belong to; update only the active org.
CREATE POLICY organizations_read ON app.organizations FOR SELECT TO app_api, app_worker
  USING (
    id = app.current_org_id()
    OR EXISTS (SELECT 1 FROM app.memberships m WHERE m.org_id = organizations.id AND m.user_id = app.current_user_id())
  );
CREATE POLICY organizations_update ON app.organizations FOR UPDATE TO app_api, app_worker
  USING (id = app.current_org_id()) WITH CHECK (id = app.current_org_id());

-- workspaces: read the active org's workspaces or ones you are a member of; write in the active org.
CREATE POLICY workspaces_read ON app.workspaces FOR SELECT TO app_api, app_worker
  USING (
    org_id = app.current_org_id()
    OR EXISTS (SELECT 1 FROM app.memberships m WHERE m.workspace_id = workspaces.id AND m.user_id = app.current_user_id())
  );
CREATE POLICY workspaces_write ON app.workspaces FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());

-- memberships: full access within the active org; a user can always list their own memberships
-- (needed before an org is chosen). The self policy never references organizations (no recursion).
CREATE POLICY memberships_tenant ON app.memberships FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
CREATE POLICY memberships_self_read ON app.memberships FOR SELECT TO app_api, app_worker
  USING (user_id = app.current_user_id());

-- user_profiles: only your own row.
CREATE POLICY user_profiles_self ON app.user_profiles FOR ALL TO app_api
  USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id());

-- sources: global catalogue, readable by all app roles, writable by workers.
CREATE POLICY sources_read ON app.sources FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY sources_worker_write ON app.sources FOR ALL TO app_worker USING (true) WITH CHECK (true);

-- Plain tenant tables.
CREATE POLICY research_jobs_tenant ON app.research_jobs FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
CREATE POLICY credit_ledger_tenant ON app.credit_ledger FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
CREATE POLICY usage_unit_keys_tenant ON app.usage_unit_keys FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
CREATE POLICY usage_events_tenant ON app.usage_events FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
-- audit_logs: tenants see/insert rows of the active org only; org-less rows come from definer functions.
CREATE POLICY audit_logs_tenant ON app.audit_logs FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());

-- ---------------------------------------------------------------- privileged entry points
-- All: SECURITY DEFINER owned by postgres (bypasses RLS), fixed search_path, EXECUTE for app_api only.

CREATE FUNCTION app.is_platform_staff(p_user_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce((SELECT is_platform_staff FROM app.user_profiles WHERE user_id = p_user_id), false) $$;

-- Creates an org, its default workspace and the caller's owner membership (+ audit row).
-- The caller must be the user in app.user_id (set by the API from a verified JWT) and must have a
-- confirmed email. IDs are UUID v7 generated by the API.
CREATE FUNCTION app.bootstrap_org(
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
  IF NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = p_user_id AND u.email_confirmed_at IS NOT NULL) THEN
    RAISE EXCEPTION 'bootstrap_org: user missing or email not confirmed' USING ERRCODE = 'insufficient_privilege';
  END IF;
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

-- Platform staff only: read-only org list for the admin shell (audited).
CREATE FUNCTION app.admin_list_orgs(p_audit_id uuid, p_limit integer DEFAULT 50, p_after uuid DEFAULT NULL)
RETURNS TABLE (id uuid, name text, slug text, plan text, region text, created_at timestamptz, member_count bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_list_orgs: platform staff only' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.orgs.listed', jsonb_build_object('limit', p_limit, 'after', p_after));
  RETURN QUERY
    SELECT o.id, o.name, o.slug, o.plan, o.region, o.created_at,
           (SELECT count(DISTINCT m.user_id) FROM app.memberships m WHERE m.org_id = o.id)
    FROM app.organizations o
    WHERE p_after IS NULL OR o.id > p_after
    ORDER BY o.id
    LIMIT least(greatest(p_limit, 1), 200);
END;
$$;

-- Platform staff only: read-only user list for the admin shell (audited).
CREATE FUNCTION app.admin_list_users(p_audit_id uuid, p_limit integer DEFAULT 50, p_after uuid DEFAULT NULL)
RETURNS TABLE (id uuid, email text, created_at timestamptz, email_confirmed_at timestamptz,
               last_sign_in_at timestamptz, is_platform_staff boolean, org_count bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
BEGIN
  IF v_actor IS NULL OR NOT app.is_platform_staff(v_actor) THEN
    RAISE EXCEPTION 'admin_list_users: platform staff only' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.users.listed', jsonb_build_object('limit', p_limit, 'after', p_after));
  RETURN QUERY
    SELECT u.id, u.email::text, u.created_at, u.email_confirmed_at, u.last_sign_in_at,
           coalesce(p.is_platform_staff, false),
           (SELECT count(DISTINCT m.org_id) FROM app.memberships m WHERE m.user_id = u.id)
    FROM auth.users u
    LEFT JOIN app.user_profiles p ON p.user_id = u.id
    WHERE p_after IS NULL OR u.id > p_after
    ORDER BY u.id
    LIMIT least(greatest(p_limit, 1), 200);
END;
$$;

REVOKE ALL ON FUNCTION app.is_platform_staff(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_list_orgs(uuid, integer, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_list_users(uuid, integer, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_list_orgs(uuid, integer, uuid) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_list_users(uuid, integer, uuid) TO app_api;
