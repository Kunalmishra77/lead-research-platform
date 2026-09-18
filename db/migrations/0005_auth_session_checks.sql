-- Session revocation + distinct error codes for org bootstrap (task 1.7 review).

-- True while the caller's Supabase session exists (not signed out, not expired by not_after) and the
-- user is neither banned nor deleted. Access tokens stay cryptographically valid until they expire;
-- this lets the API reject revoked sessions early on tenant-scoped requests.
CREATE FUNCTION app.session_is_active(p_session_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM auth.sessions s
    JOIN auth.users u ON u.id = s.user_id
    WHERE s.id = p_session_id
      AND s.user_id = app.current_user_id()
      AND (s.not_after IS NULL OR s.not_after > now())
      AND u.deleted_at IS NULL
      AND (u.banned_until IS NULL OR u.banned_until <= now())
  )
$$;

REVOKE ALL ON FUNCTION app.session_is_active(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.session_is_active(uuid) TO app_api;

-- Distinct SQLSTATEs so the API can report the real reason:
--   42501 caller mismatch · LF001 email not confirmed · LF002 user banned/deleted/missing · LF003 org limit
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
  v_confirmed timestamptz;
  v_deleted timestamptz;
  v_banned timestamptz;
  v_owned integer;
BEGIN
  IF p_user_id IS NULL OR p_user_id IS DISTINCT FROM app.current_user_id() THEN
    RAISE EXCEPTION 'bootstrap_org: caller does not match app.user_id' USING ERRCODE = '42501';
  END IF;
  SELECT u.email_confirmed_at, u.deleted_at, u.banned_until INTO v_confirmed, v_deleted, v_banned
  FROM auth.users u WHERE u.id = p_user_id;
  IF NOT FOUND OR v_deleted IS NOT NULL OR (v_banned IS NOT NULL AND v_banned > now()) THEN
    RAISE EXCEPTION 'bootstrap_org: user is missing, banned or deleted' USING ERRCODE = 'LF002';
  END IF;
  IF v_confirmed IS NULL THEN
    RAISE EXCEPTION 'bootstrap_org: email not confirmed' USING ERRCODE = 'LF001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('bootstrap_org:' || p_user_id::text, 0));
  SELECT count(*) INTO v_owned FROM app.memberships WHERE user_id = p_user_id AND role = 'owner';
  IF v_owned >= 10 THEN
    RAISE EXCEPTION 'bootstrap_org: organization limit reached' USING ERRCODE = 'LF003';
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
