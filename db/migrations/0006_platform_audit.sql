-- Org-less (platform) audit events written by the API, e.g. auth.session_started (task 1.13).
-- Tenant events are inserted directly under RLS (audit_logs_tenant_insert); this function exists only
-- for events without an org, restricted to the calling user and to the `auth.` namespace.
CREATE FUNCTION app.record_platform_audit(
  p_audit_id uuid,
  p_action text,
  p_meta jsonb DEFAULT '{}'::jsonb,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_actor uuid := app.current_user_id();
BEGIN
  IF v_actor IS NULL THEN
    RAISE EXCEPTION 'record_platform_audit: app.user_id is not set' USING ERRCODE = '42501';
  END IF;
  IF p_action !~ '^auth\.[a-z_]{3,40}$' THEN
    RAISE EXCEPTION 'record_platform_audit: action % not allowed', p_action USING ERRCODE = '22023';
  END IF;
  IF pg_column_size(p_meta) > 4096 THEN
    RAISE EXCEPTION 'record_platform_audit: meta too large' USING ERRCODE = '22023';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, p_action, p_ip, left(p_user_agent, 512), coalesce(p_meta, '{}'::jsonb));
END;
$$;

REVOKE ALL ON FUNCTION app.record_platform_audit(uuid, text, jsonb, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_platform_audit(uuid, text, jsonb, inet, text) TO app_api;

-- Team directory: members of one workspace with email + name (auth.users and other users'
-- profiles are not readable by app roles). Only for a member of that workspace, in the active org.
CREATE FUNCTION app.workspace_members(p_workspace_id uuid)
RETURNS TABLE (user_id uuid, email text, full_name text, role app.membership_role, joined_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM app.memberships m
    WHERE m.workspace_id = p_workspace_id
      AND m.org_id = app.current_org_id()
      AND m.user_id = app.current_user_id()
  ) THEN
    RAISE EXCEPTION 'workspace_members: not a member of this workspace' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
    SELECT m.user_id, u.email::text, p.full_name, m.role, m.created_at
    FROM app.memberships m
    JOIN auth.users u ON u.id = m.user_id
    LEFT JOIN app.user_profiles p ON p.user_id = m.user_id
    WHERE m.workspace_id = p_workspace_id AND m.org_id = app.current_org_id()
    ORDER BY m.created_at, m.user_id;
END;
$$;

REVOKE ALL ON FUNCTION app.workspace_members(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.workspace_members(uuid) TO app_api;
