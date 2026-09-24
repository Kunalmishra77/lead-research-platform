-- Security for app.leads (ADR-0012, ADR-0003).
--
-- `leads` is the tenant side of the shared graph: `companies` says what public sources reported,
-- this says which workspace was handed that company and by which job. So unlike the graph tables
-- it is isolated per org, and unlike `research_tasks` the API writes it too — a user changing a
-- lead's status or correcting a value is tenant work, and those corrections never reach the graph
-- until they are reviewed and re-observed (ADR-0007).

CREATE TRIGGER leads_updated_at BEFORE UPDATE ON app.leads
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

ALTER TABLE app.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.leads FORCE ROW LEVEL SECURITY;

CREATE POLICY leads_tenant ON app.leads FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id())
  WITH CHECK (org_id = app.current_org_id());

REVOKE ALL ON app.leads FROM app_api, app_worker;

-- The API owns the lead's workflow: a user sets status, assigns it, corrects a value, records
-- that they made contact. It may not rewrite which company a lead is, which workspace it belongs
-- to, or which job delivered it — those are facts about how the lead came to exist.
GRANT SELECT, INSERT ON app.leads TO app_api;
GRANT UPDATE (status, assignee_user_id, overrides, custom_fields, contacted_at, score,
              score_breakdown)
  ON app.leads TO app_api;

-- The worker only ever hands a lead over. It never edits one afterwards: a discovery task that
-- runs again must not reset a status the customer has since changed, which is why there is no
-- UPDATE grant here at all and the executor inserts with ON CONFLICT DO NOTHING.
GRANT SELECT, INSERT ON app.leads TO app_worker;

COMMENT ON TABLE app.leads IS
  'A company a workspace has been given (docs/04, ADR-0012). The tenant side of the global '
  'graph, and what "delivered new lead" counts for billing: one row per company per workspace.';
