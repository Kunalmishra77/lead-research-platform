-- Security and backfill for app.research_job_leads (ADR-0015, ADR-0012, ADR-0003).
--
-- The table answers "which leads did this job surface?", which `leads.research_job_id` cannot:
-- that column holds which job delivered a lead *first*, and a customer re-running a search
-- re-finds leads without delivering any. It is tenant data, so it is isolated like `leads`.

ALTER TABLE app.research_job_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.research_job_leads FORCE ROW LEVEL SECURITY;

CREATE POLICY research_job_leads_tenant ON app.research_job_leads FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id())
  WITH CHECK (org_id = app.current_org_id());

REVOKE ALL ON app.research_job_leads FROM app_api, app_worker;

-- Read-only for the API: the grid joins it, nothing in the product edits it. A link row records
-- what a job found at the moment it ran, and no later user action makes that untrue.
GRANT SELECT ON app.research_job_leads TO app_api;

-- The worker writes one row per candidate it stores and never revisits it. No UPDATE grant, for
-- the reason ADR-0012 gave about `leads`: a task that runs again must not edit history, and
-- `is_new` in particular is what an invoice was built from.
GRANT SELECT, INSERT ON app.research_job_leads TO app_worker;

-- Backfill. Every lead that names a job exists *because* that job delivered it, so `is_new` is
-- true by construction. Jobs that re-found a lead before this table existed left no trace
-- anywhere and none is invented here -- their counts stay as they were.
INSERT INTO app.research_job_leads (org_id, research_job_id, lead_id, is_new, created_at)
SELECT org_id, research_job_id, id, true, created_at
FROM app.leads
WHERE research_job_id IS NOT NULL
ON CONFLICT DO NOTHING;

COMMENT ON TABLE app.research_job_leads IS
  'Which leads a research job surfaced, new or already held (ADR-0015). The grid reads this; '
  'billing counts sum(is_new). leads.research_job_id keeps its own meaning: delivered first by.';

COMMENT ON COLUMN app.research_job_leads.is_new IS
  'Was this lead new to the workspace when this job ran? Stored, not derived: only knowable at '
  'write time, and a later job must not change what an earlier invoice said.';
