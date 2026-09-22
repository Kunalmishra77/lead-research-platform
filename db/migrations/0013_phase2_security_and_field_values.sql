-- Phase 2 task 2.1 (ADR-0007): field_values (partitioned, keep in sync with db/schema/partitioned.ts),
-- RLS on every new table, least-privilege grants and updated_at triggers.

-- ---------------------------------------------------------------- field_values
CREATE TABLE app.field_values (
  id uuid NOT NULL,
  entity_type app.entity_type NOT NULL,
  entity_id uuid NOT NULL,
  field text NOT NULL,
  value jsonb NOT NULL,
  source_id uuid NOT NULL,
  source_url text NOT NULL,
  raw_document_id uuid,
  method app.value_method NOT NULL,
  derivation text,
  confidence real NOT NULL,
  -- When the source showed the value (not ingestion time), so there is deliberately no default.
  observed_at timestamptz NOT NULL,
  is_current boolean NOT NULL DEFAULT true,
  model text,
  prompt_version text,
  CONSTRAINT field_values_pkey PRIMARY KEY (id, observed_at),
  CONSTRAINT field_values_source_fk FOREIGN KEY (source_id) REFERENCES app.sources (id),
  CONSTRAINT field_values_field_format CHECK (field ~ '^[a-z][a-z0-9_]{0,63}$'),
  CONSTRAINT field_values_confidence_range CHECK (confidence BETWEEN 0 AND 1),
  CONSTRAINT field_values_source_url_format CHECK (source_url ~ '^https?://' AND length(source_url) <= 2048),
  CONSTRAINT field_values_derivation_known CHECK (derivation IS NULL OR derivation IN ('found', 'derived_pattern', 'provider', 'user')),
  -- AI honesty (CLAUDE.md): AI-produced values always carry the model and prompt version.
  CONSTRAINT field_values_ai_traceable CHECK (method <> 'ai' OR (model IS NOT NULL AND prompt_version IS NOT NULL)),
  -- AI never produces contact values (CLAUDE.md): they must be found in a source or derived by code.
  CONSTRAINT field_values_ai_no_contacts CHECK (NOT (method = 'ai' AND field IN ('email', 'phone', 'whatsapp')))
) PARTITION BY RANGE (observed_at);

CREATE INDEX field_values_current_idx ON app.field_values (entity_type, entity_id, field) WHERE is_current;

SELECT app.ensure_monthly_partitions('app.field_values', 12);

-- Replace the nightly job so it also maintains field_values partitions.
DO $cron$
BEGIN
  -- Unschedule only if present, so a missing job never skips the schedule call below.
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'app-ensure-monthly-partitions') THEN
    PERFORM cron.unschedule('app-ensure-monthly-partitions');
  END IF;
  PERFORM cron.schedule(
    'app-ensure-monthly-partitions',
    '17 3 * * *',
    $job$SELECT app.ensure_monthly_partitions('app.audit_logs', 3); SELECT app.ensure_monthly_partitions('app.usage_events', 3); SELECT app.ensure_monthly_partitions('app.field_values', 3);$job$
  );
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unavailable (%); schedule app.ensure_monthly_partitions externally', SQLERRM;
END
$cron$;

-- ---------------------------------------------------------------- updated_at
CREATE TRIGGER industries_updated_at BEFORE UPDATE ON app.industries FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER geo_areas_updated_at BEFORE UPDATE ON app.geo_areas FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER companies_updated_at BEFORE UPDATE ON app.companies FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER company_locations_updated_at BEFORE UPDATE ON app.company_locations FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER research_tasks_updated_at BEFORE UPDATE ON app.research_tasks FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- ---------------------------------------------------------------- row level security
ALTER TABLE app.industries ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.industries FORCE ROW LEVEL SECURITY;
ALTER TABLE app.geo_areas ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.geo_areas FORCE ROW LEVEL SECURITY;
ALTER TABLE app.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.companies FORCE ROW LEVEL SECURITY;
ALTER TABLE app.company_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.company_domains FORCE ROW LEVEL SECURITY;
ALTER TABLE app.company_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.company_locations FORCE ROW LEVEL SECURITY;
ALTER TABLE app.field_values ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.field_values FORCE ROW LEVEL SECURITY;
ALTER TABLE app.searches ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.searches FORCE ROW LEVEL SECURITY;
ALTER TABLE app.research_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.research_tasks FORCE ROW LEVEL SECURITY;

-- Reference data (seeded by the owner): readable by both app roles, written by nobody at runtime.
CREATE POLICY industries_read ON app.industries FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY geo_areas_read ON app.geo_areas FOR SELECT TO app_api, app_worker USING (true);

-- Global company graph: readable by both roles; only workers write (public sources only).
CREATE POLICY companies_read ON app.companies FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY companies_worker_write ON app.companies FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY company_domains_read ON app.company_domains FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY company_domains_worker_write ON app.company_domains FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY company_locations_read ON app.company_locations FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY company_locations_worker_write ON app.company_locations FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY field_values_read ON app.field_values FOR SELECT TO app_api, app_worker USING (true);
CREATE POLICY field_values_worker_write ON app.field_values FOR ALL TO app_worker USING (true) WITH CHECK (true);

-- Tenant tables.
CREATE POLICY searches_tenant ON app.searches FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());
CREATE POLICY research_tasks_tenant ON app.research_tasks FOR ALL TO app_api, app_worker
  USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id());

-- ---------------------------------------------------------------- privileges
-- Reference data: read-only for both roles.
REVOKE ALL ON app.industries, app.geo_areas FROM app_api, app_worker;
GRANT SELECT ON app.industries, app.geo_areas TO app_api, app_worker;

-- Graph: the API only reads; workers upsert. Nobody deletes (merges are recorded, not deleted).
REVOKE ALL ON app.companies, app.company_domains, app.company_locations FROM app_api, app_worker;
GRANT SELECT ON app.companies, app.company_domains, app.company_locations TO app_api;
GRANT SELECT, INSERT, UPDATE ON app.companies, app.company_domains, app.company_locations TO app_worker;

-- field_values: append-only observations; workers may only flip is_current.
REVOKE ALL ON app.field_values FROM app_api, app_worker;
GRANT SELECT ON app.field_values TO app_api;
GRANT SELECT, INSERT ON app.field_values TO app_worker;
GRANT UPDATE (is_current) ON app.field_values TO app_worker;

-- searches: created and read by the API; workers read the spec. Immutable once written.
REVOKE ALL ON app.searches FROM app_api, app_worker;
GRANT SELECT, INSERT ON app.searches TO app_api;
GRANT SELECT ON app.searches TO app_worker;

-- research_tasks: the planner/executor (workers) create and run tasks; the API reads them.
REVOKE ALL ON app.research_tasks FROM app_api, app_worker;
GRANT SELECT ON app.research_tasks TO app_api;
GRANT SELECT, INSERT ON app.research_tasks TO app_worker;
GRANT UPDATE (status, attempts, cost_micros, output, error_class, started_at, finished_at)
  ON app.research_tasks TO app_worker;
