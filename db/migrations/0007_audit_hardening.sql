-- Task 1.13 review: explicit allowlist for org-less audit actions and a strict size cap on meta.
CREATE OR REPLACE FUNCTION app.record_platform_audit(
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
  IF p_action NOT IN ('auth.session_started', 'auth.signed_out') THEN
    RAISE EXCEPTION 'record_platform_audit: action % not allowed', p_action USING ERRCODE = '22023';
  END IF;
  IF octet_length(coalesce(p_meta, '{}'::jsonb)::text) > 4096 THEN
    RAISE EXCEPTION 'record_platform_audit: meta too large' USING ERRCODE = '22023';
  END IF;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, ip, user_agent, meta)
    VALUES (p_audit_id, NULL, v_actor, p_action, p_ip, left(p_user_agent, 512), coalesce(p_meta, '{}'::jsonb));
END;
$$;

REVOKE ALL ON FUNCTION app.record_platform_audit(uuid, text, jsonb, inet, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_platform_audit(uuid, text, jsonb, inet, text) TO app_api;
