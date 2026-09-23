-- Phase 2 task 2.5 (docs/11): credit ledger operations.
-- Money rules enforced here, not in the API:
--   * the reservation is derived from the append-only ledger, never from a column the API can write
--   * usage is always counted within the job's own org (a tenant cannot spend another org's budget)
--   * consume is incremental, so usage recorded after a settle is still booked, never twice
--   * a job is charged at most what it reserved

-- ---------------------------------------------------------------- reference data
ALTER TABLE app.credit_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.credit_rates FORCE ROW LEVEL SECURITY;
ALTER TABLE app.plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.plans FORCE ROW LEVEL SECURITY;

CREATE TRIGGER credit_rates_updated_at BEFORE UPDATE ON app.credit_rates FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER plans_updated_at BEFORE UPDATE ON app.plans FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE POLICY credit_rates_read ON app.credit_rates FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY plans_read ON app.plans FOR SELECT TO app_api, app_worker USING (true);

REVOKE ALL ON app.credit_rates, app.plans FROM app_api, app_worker;
GRANT SELECT ON app.credit_rates, app.plans TO app_api, app_worker;

-- Plans must exist before organizations can reference them (seeds keep these rows current).
INSERT INTO app.plans (plan, name, signup_credits, monthly_credits, seats) VALUES
  ('free', 'Free', 50, 0, 1),
  ('starter', 'Starter', 0, 1500, 2),
  ('growth', 'Growth', 0, 6000, 5),
  ('pro', 'Pro / Agency', 0, 20000, 10),
  ('enterprise', 'Enterprise', 0, 0, 25)
ON CONFLICT (plan) DO NOTHING;

-- An unknown plan must fail loudly instead of silently granting nothing.
ALTER TABLE app.organizations
  ADD CONSTRAINT organizations_plan_fk FOREIGN KEY (plan) REFERENCES app.plans (plan);

-- Tenant integrity of usage already comes from usage_events_job_org_fk (migration 0004):
-- (research_job_id, org_id) -> research_jobs(id, org_id), so usage can only be attached to a job
-- of the same org. credit_settle still filters by org as defence in depth.

-- ---------------------------------------------------------------- least privilege on jobs
-- The API creates and cancels jobs; workers report progress. Credit columns belong to the
-- functions below (they run as the owner), so neither role may write them directly.
REVOKE UPDATE, DELETE ON app.research_jobs FROM app_api, app_worker;
GRANT UPDATE (status, error_class, finished_at) ON app.research_jobs TO app_api;
GRANT UPDATE (status, progress, error_class, started_at, finished_at) ON app.research_jobs TO app_worker;

-- ---------------------------------------------------------------- ledger primitives
-- Appends one ledger row and updates the cached balance. Callers hold the organization row lock.
CREATE FUNCTION app.credit_post(
  p_ledger_id uuid,
  p_org_id uuid,
  p_delta bigint,
  p_reason app.credit_reason,
  p_ref_type text,
  p_ref_id uuid,
  p_actor uuid
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_balance bigint;
BEGIN
  IF p_delta = 0 THEN
    RAISE EXCEPTION 'credit_post: delta must not be zero' USING ERRCODE = '22023';
  END IF;
  UPDATE app.organizations SET credits_balance = credits_balance + p_delta
    WHERE id = p_org_id RETURNING credits_balance INTO v_balance;
  IF v_balance IS NULL THEN
    RAISE EXCEPTION 'credit_post: unknown organization' USING ERRCODE = '23503';
  END IF;
  IF v_balance < 0 THEN
    RAISE EXCEPTION 'credit_post: balance would go negative' USING ERRCODE = 'LF402';
  END IF;
  INSERT INTO app.credit_ledger (id, org_id, delta, balance_after, reason, ref_type, ref_id, created_by)
    VALUES (p_ledger_id, p_org_id, p_delta, v_balance, p_reason, p_ref_type, p_ref_id, p_actor);
  RETURN v_balance;
END;
$$;

REVOKE ALL ON FUNCTION app.credit_post(uuid, uuid, bigint, app.credit_reason, text, uuid, uuid) FROM PUBLIC;

/** Credits already reserved for a job, straight from the append-only ledger. */
CREATE FUNCTION app.credit_reserved_for(p_org_id uuid, p_job_id uuid) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT coalesce(sum(-delta), 0)::bigint FROM app.credit_ledger
  WHERE org_id = p_org_id AND ref_type = 'research_job' AND ref_id = p_job_id AND reason = 'reserve'
$$;

REVOKE ALL ON FUNCTION app.credit_reserved_for(uuid, uuid) FROM PUBLIC;

-- ---------------------------------------------------------------- reserve
-- Holds credits for a job of the active org before it starts. LF402 when the balance is too low
-- (the API answers 402). Idempotent per job: a retried create never debits twice.
CREATE FUNCTION app.credit_reserve(p_job_id uuid, p_amount bigint, p_ledger_id uuid)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_org uuid;
  v_actor uuid := app.current_user_id();
  v_balance bigint;
BEGIN
  IF p_amount <= 0 OR p_amount > 2147483647 THEN
    RAISE EXCEPTION 'credit_reserve: amount out of range' USING ERRCODE = '22023';
  END IF;
  SELECT org_id INTO v_org FROM app.research_jobs WHERE id = p_job_id;
  IF v_org IS NULL OR v_org IS DISTINCT FROM app.current_org_id() THEN
    RAISE EXCEPTION 'credit_reserve: job is not in the active org' USING ERRCODE = '42501';
  END IF;
  -- Serialize reservations of this org so two jobs cannot both pass the balance check.
  SELECT credits_balance INTO v_balance FROM app.organizations WHERE id = v_org FOR UPDATE;
  IF app.credit_reserved_for(v_org, p_job_id) > 0 THEN
    RETURN v_balance;
  END IF;
  IF v_balance < p_amount THEN
    RAISE EXCEPTION 'credit_reserve: insufficient credits' USING ERRCODE = 'LF402';
  END IF;
  v_balance := app.credit_post(p_ledger_id, v_org, -p_amount, 'reserve', 'research_job', p_job_id, v_actor);
  UPDATE app.research_jobs SET credits_reserved = p_amount, credit_budget = p_amount WHERE id = p_job_id;
  RETURN v_balance;
END;
$$;

-- ---------------------------------------------------------------- settle
-- Books what the job delivered and returns the unused reservation. Safe to call repeatedly: the
-- reservation is released once, and `consume` only ever books usage that is not booked yet, so
-- usage recorded late is still charged (never twice, never beyond the reservation).
CREATE FUNCTION app.credit_settle(p_job_id uuid, p_consume_id uuid, p_release_id uuid)
RETURNS TABLE (consumed bigint, released bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_org uuid;
  v_actor uuid := app.current_user_id();
  v_reserved bigint;
  v_settled timestamptz;
  v_used bigint;
  v_consumed bigint;
  v_delta bigint;
BEGIN
  SELECT org_id INTO v_org FROM app.research_jobs WHERE id = p_job_id;
  IF v_org IS NULL OR v_org IS DISTINCT FROM app.current_org_id() THEN
    RAISE EXCEPTION 'credit_settle: job is not in the active org' USING ERRCODE = '42501';
  END IF;
  -- Lock the organization (balance) and the job (settled_at) before reading any total.
  PERFORM 1 FROM app.organizations WHERE id = v_org FOR UPDATE;
  SELECT settled_at INTO v_settled FROM app.research_jobs WHERE id = p_job_id FOR UPDATE;

  v_reserved := app.credit_reserved_for(v_org, p_job_id);
  IF v_reserved = 0 THEN
    RAISE EXCEPTION 'credit_settle: job has no reservation' USING ERRCODE = '22023';
  END IF;

  -- Usage of this job in this org only, capped by the reservation.
  SELECT least(coalesce(sum(credits), 0), v_reserved) INTO v_used
  FROM app.usage_events WHERE research_job_id = p_job_id AND org_id = v_org;
  SELECT coalesce(sum(-delta), 0) INTO v_consumed FROM app.credit_ledger
  WHERE org_id = v_org AND ref_type = 'research_job' AND ref_id = p_job_id AND reason = 'consume';

  v_delta := v_used - v_consumed;
  IF v_delta > 0 THEN
    PERFORM app.credit_post(p_consume_id, v_org, -v_delta, 'consume', 'research_job', p_job_id, v_actor);
  END IF;

  IF v_settled IS NULL THEN
    PERFORM app.credit_post(p_release_id, v_org, v_reserved, 'release', 'research_job', p_job_id, v_actor);
    UPDATE app.research_jobs SET settled_at = now(), credits_reserved = 0, credits_used = v_used
      WHERE id = p_job_id;
    RETURN QUERY SELECT v_delta, v_reserved - v_used;
  END IF;
  UPDATE app.research_jobs SET credits_used = v_used WHERE id = p_job_id;
  RETURN QUERY SELECT v_delta, 0::bigint;
END;
$$;

REVOKE ALL ON FUNCTION app.credit_reserve(uuid, bigint, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.credit_settle(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.credit_reserve(uuid, bigint, uuid) TO app_api;
GRANT EXECUTE ON FUNCTION app.credit_settle(uuid, uuid, uuid) TO app_api;

-- ---------------------------------------------------------------- signup grant
-- Same as 0005 plus the plan's signup credits, booked through the ledger (docs/11 grant).
-- The signature gains p_grant_id (UUID v7 from the app, like every other id we store).
DROP FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid);

CREATE FUNCTION app.bootstrap_org(
  p_user_id uuid,
  p_org_id uuid,
  p_org_name text,
  p_org_slug text,
  p_workspace_id uuid,
  p_workspace_name text,
  p_membership_id uuid,
  p_audit_id uuid,
  p_grant_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_confirmed timestamptz;
  v_deleted timestamptz;
  v_banned timestamptz;
  v_owned integer;
  v_plan text;
  v_signup integer;
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

  INSERT INTO app.organizations (id, name, slug) VALUES (p_org_id, p_org_name, p_org_slug)
    RETURNING plan INTO v_plan;
  INSERT INTO app.workspaces (id, org_id, name) VALUES (p_workspace_id, p_org_id, p_workspace_name);
  INSERT INTO app.memberships (id, org_id, workspace_id, user_id, role)
    VALUES (p_membership_id, p_org_id, p_workspace_id, p_user_id, 'owner');
  INSERT INTO app.user_profiles (user_id) VALUES (p_user_id) ON CONFLICT (user_id) DO NOTHING;
  INSERT INTO app.audit_logs (id, org_id, actor_user_id, action, target_type, target_id, meta)
    VALUES (p_audit_id, p_org_id, p_user_id, 'org.created', 'organization', p_org_id::text,
            jsonb_build_object('workspace_id', p_workspace_id));

  -- The plan FK guarantees a row here, so a missing grant is a bug, not a silent zero.
  SELECT signup_credits INTO STRICT v_signup FROM app.plans WHERE plan = v_plan;
  IF v_signup > 0 THEN
    PERFORM app.credit_post(p_grant_id, p_org_id, v_signup, 'grant', 'plan', NULL, p_user_id);
  END IF;
END;
$$;

REVOKE ALL ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.bootstrap_org(uuid, uuid, text, text, uuid, text, uuid, uuid, uuid) TO app_api;
