-- ADR-0014. A job the worker finishes was settled by nobody: the worker's grant excludes the
-- credit columns and app.credit_settle is granted to app_api only, while the API settles only on
-- cancel and on failure. Three jobs had run, all three showed credits_used 0 and settled_at null,
-- app.usage_events held 589 credits of metered usage, and app.credit_ledger held no consume or
-- release row at all.
--
-- So settlement moves into the database, where it stops depending on any app process being alive.

-- ---------------------------------------------------------------- split
-- The body of credit_settle, with the org as an argument rather than read from the session, so a
-- caller with no tenant session (the sweeper) can use it. Granted to nobody: owner only.
--
-- p_require_reservation keeps the API's behaviour exactly as it was. A tenant call naming a job
-- with no reservation is a confused caller and still raises. For the sweeper it is just an old row
-- -- two of the three jobs above carry credits_reserved 80 with no reserve in the ledger, from
-- development before the ledger was wired -- and a sweeper that raised on those would fail on its
-- first row for ever and settle nothing.
--
-- Note what such a row then gets: v_used is capped by the reservation, so a job with no
-- reservation consumes nothing. That is the safety property working, not a special case. Charging
-- 582 credits for development runs whose credits were never held would be the wrong repair.
CREATE FUNCTION app.credit_settle_job(
  p_job_id uuid,
  p_org_id uuid,
  p_consume_id uuid,
  p_release_id uuid,
  p_actor uuid DEFAULT NULL,
  p_require_reservation boolean DEFAULT true
)
RETURNS TABLE (consumed bigint, released bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_reserved bigint;
  v_settled timestamptz;
  v_used bigint;
  v_consumed bigint;
  v_delta bigint;
BEGIN
  -- Lock the organization (balance) and the job (settled_at) before reading any total.
  PERFORM 1 FROM app.organizations WHERE id = p_org_id FOR UPDATE;
  SELECT settled_at INTO v_settled FROM app.research_jobs WHERE id = p_job_id FOR UPDATE;

  v_reserved := app.credit_reserved_for(p_org_id, p_job_id);
  IF v_reserved = 0 AND p_require_reservation THEN
    RAISE EXCEPTION 'credit_settle: job has no reservation' USING ERRCODE = '22023';
  END IF;

  -- Usage of this job in this org only, capped by the reservation.
  SELECT least(coalesce(sum(credits), 0), v_reserved) INTO v_used
  FROM app.usage_events WHERE research_job_id = p_job_id AND org_id = p_org_id;
  SELECT coalesce(sum(-delta), 0) INTO v_consumed FROM app.credit_ledger
  WHERE org_id = p_org_id AND ref_type = 'research_job' AND ref_id = p_job_id AND reason = 'consume';

  v_delta := v_used - v_consumed;
  IF v_delta > 0 THEN
    PERFORM app.credit_post(p_consume_id, p_org_id, -v_delta, 'consume', 'research_job', p_job_id,
                            p_actor);
  END IF;

  IF v_settled IS NULL THEN
    -- credit_post refuses a zero delta, so a job with nothing held gets no release row: there is
    -- nothing to give back, and a ledger entry saying so would be noise.
    IF v_reserved > 0 THEN
      PERFORM app.credit_post(p_release_id, p_org_id, v_reserved, 'release', 'research_job',
                              p_job_id, p_actor);
    END IF;
    UPDATE app.research_jobs SET settled_at = now(), credits_reserved = 0, credits_used = v_used
      WHERE id = p_job_id;
    RETURN QUERY SELECT v_delta, v_reserved - v_used;
  END IF;
  UPDATE app.research_jobs SET credits_used = v_used WHERE id = p_job_id;
  RETURN QUERY SELECT v_delta, 0::bigint;
END;
$$;

REVOKE ALL ON FUNCTION app.credit_settle_job(uuid, uuid, uuid, uuid, uuid, boolean) FROM PUBLIC;

-- The tenant-facing call keeps its signature, its grant and its behaviour: it still resolves the
-- job's org, still refuses a job outside the active org, and still raises on a job with no
-- reservation. All it loses is the body.
CREATE OR REPLACE FUNCTION app.credit_settle(p_job_id uuid, p_consume_id uuid, p_release_id uuid)
RETURNS TABLE (consumed bigint, released bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_org uuid;
BEGIN
  SELECT org_id INTO v_org FROM app.research_jobs WHERE id = p_job_id;
  IF v_org IS NULL OR v_org IS DISTINCT FROM app.current_org_id() THEN
    RAISE EXCEPTION 'credit_settle: job is not in the active org' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY SELECT * FROM app.credit_settle_job(p_job_id, v_org, p_consume_id, p_release_id,
                                                   app.current_user_id(), true);
END;
$$;

-- ---------------------------------------------------------------- sweeper
-- Settles terminal jobs that nothing else settled.
--
-- A PROCEDURE, not a function, so each job can commit on its own: one job that cannot settle must
-- not roll back the ones already closed behind it.
--
-- Deliberately NOT security definer, which took a probe against the live server to establish:
-- PostgreSQL refuses transaction control inside a SECURITY DEFINER routine with
-- `invalid transaction termination`, so a definer procedure cannot commit per job at all. It does
-- not need to be one. pg_cron runs a job as whoever scheduled it, the migrations role is `postgres`
-- (as the two existing cron jobs confirm), and `postgres` owns these tables. Revoked from PUBLIC so
-- nothing else can call it.
--
-- The due ids are collected into arrays first rather than looped over a cursor, because a cursor
-- cannot be held across the COMMIT inside the loop.
CREATE PROCEDURE app.settle_finished_jobs(p_limit integer DEFAULT 200)
LANGUAGE plpgsql
AS $$
DECLARE
  v_jobs uuid[];
  v_orgs uuid[];
  v_settled integer := 0;
  v_failed integer := 0;
  i integer;
BEGIN
  SELECT array_agg(due.id ORDER BY due.created_at), array_agg(due.org_id ORDER BY due.created_at)
    INTO v_jobs, v_orgs
  FROM (
    SELECT id, org_id, created_at FROM app.research_jobs
    WHERE status IN ('completed', 'failed', 'cancelled') AND settled_at IS NULL
    ORDER BY created_at
    LIMIT greatest(coalesce(p_limit, 200), 1)
  ) due;

  IF v_jobs IS NULL THEN RETURN; END IF;

  FOR i IN 1 .. array_length(v_jobs, 1) LOOP
    BEGIN
      -- gen_random_uuid rather than a v7 id: this database has no v7 generator, and a ledger row's
      -- id is never used as a cursor -- created_at orders the ledger. A hand-rolled bit generator
      -- here would be a worse trade than a v4 id with this sentence next to it.
      PERFORM app.credit_settle_job(v_jobs[i], v_orgs[i],
                                    gen_random_uuid(), gen_random_uuid(), NULL, false);
      v_settled := v_settled + 1;
    EXCEPTION WHEN OTHERS THEN
      -- The subtransaction rolls back and the loop carries on. A job that cannot settle is a thing
      -- to look at, not a reason to stop closing the books on everything behind it.
      v_failed := v_failed + 1;
      RAISE WARNING 'settle_finished_jobs: job % did not settle (%)', v_jobs[i], SQLERRM;
    END;
    COMMIT;
  END LOOP;

  RAISE NOTICE 'settle_finished_jobs: settled %, failed %', v_settled, v_failed;
END;
$$;

REVOKE ALL ON PROCEDURE app.settle_finished_jobs(integer) FROM PUBLIC;

-- The sweeper's only query. Partial, because the rows it wants are the rare ones: a job is
-- terminal-and-unsettled for about a minute of its life.
CREATE INDEX research_jobs_unsettled_idx ON app.research_jobs (created_at)
  WHERE status IN ('completed', 'failed', 'cancelled') AND settled_at IS NULL;

-- ---------------------------------------------------------------- schedule
-- Every minute. The job page shows credits, so settlement latency is visible to someone watching a
-- run finish; an hourly sweep would trade a wrong number for a stale one all afternoon.
DO $cron$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'app-settle-finished-jobs') THEN
    PERFORM cron.unschedule('app-settle-finished-jobs');
  END IF;
  PERFORM cron.schedule(
    'app-settle-finished-jobs',
    '* * * * *',
    $job$CALL app.settle_finished_jobs(200);$job$
  );
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unavailable (%); schedule app.settle_finished_jobs externally', SQLERRM;
END
$cron$;
