-- job_runs: tenant isolation and least privilege (task 1.10).
ALTER TABLE app.job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.job_runs FORCE ROW LEVEL SECURITY;

CREATE POLICY job_runs_tenant ON app.job_runs FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());

CREATE TRIGGER job_runs_updated_at BEFORE UPDATE ON app.job_runs
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- The API creates and reads runs; workers read them and report progress. Nobody deletes.
REVOKE ALL ON app.job_runs FROM app_api, app_worker;
GRANT SELECT, INSERT ON app.job_runs TO app_api;
GRANT SELECT ON app.job_runs TO app_worker;
GRANT UPDATE (status, attempts, result, error_class, error, started_at, finished_at)
  ON app.job_runs TO app_worker;
