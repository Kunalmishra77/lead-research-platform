-- The API marks a run failed when publishing its envelope fails (no orphan `queued` rows), and will
-- cancel runs later. RLS (job_runs_tenant) still scopes every update to the active org.
GRANT UPDATE (status, error_class, error, finished_at) ON app.job_runs TO app_api;
