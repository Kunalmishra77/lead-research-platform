-- Task 1.12 review: admin list calls are the most sensitive reads (every user's email), so their
-- audit rows carry the request origin like the org-less auth events (0006/0007). The old
-- signatures are dropped so there is exactly one overload per function.
DROP FUNCTION app.admin_list_orgs(uuid, integer, uuid);
DROP FUNCTION app.admin_list_users(uuid, integer, uuid);

CREATE FUNCTION app.admin_list_orgs(
  p_audit_id uuid,
  p_limit integer DEFAULT 50,
  p_after uuid DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
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
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.orgs.listed', p_ip, left(p_user_agent, 512),
            jsonb_build_object('limit', p_limit, 'after', p_after));
  RETURN QUERY
    SELECT o.id, o.name, o.slug, o.plan, o.region, o.created_at,
           (SELECT count(DISTINCT m.user_id) FROM app.memberships m WHERE m.org_id = o.id)
    FROM app.organizations o
    WHERE p_after IS NULL OR o.id > p_after
    ORDER BY o.id
    LIMIT least(greatest(p_limit, 1), 201);
END;
$$;

-- Ordered by id: auth.users ids are random (v4), so this is a stable keyset, not signup order.
CREATE FUNCTION app.admin_list_users(
  p_audit_id uuid,
  p_limit integer DEFAULT 50,
  p_after uuid DEFAULT NULL,
  p_ip inet DEFAULT NULL,
  p_user_agent text DEFAULT NULL
)
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
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, 'admin.users.listed', p_ip, left(p_user_agent, 512),
            jsonb_build_object('limit', p_limit, 'after', p_after));
  RETURN QUERY
    SELECT u.id, u.email::text, u.created_at, u.email_confirmed_at, u.last_sign_in_at,
           coalesce(p.is_platform_staff, false),
           (SELECT count(DISTINCT m.org_id) FROM app.memberships m WHERE m.user_id = u.id)
    FROM auth.users u
    LEFT JOIN app.user_profiles p ON p.user_id = u.id
    WHERE p_after IS NULL OR u.id > p_after
    ORDER BY u.id
    LIMIT least(greatest(p_limit, 1), 201);
END;
$$;

REVOKE ALL ON FUNCTION app.admin_list_orgs(uuid, integer, uuid, inet, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.admin_list_users(uuid, integer, uuid, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.admin_list_orgs(uuid, integer, uuid, inet, text) TO app_api;
GRANT EXECUTE ON FUNCTION app.admin_list_users(uuid, integer, uuid, inet, text) TO app_api;
