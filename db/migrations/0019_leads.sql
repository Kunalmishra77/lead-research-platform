CREATE TYPE "app"."lead_status" AS ENUM('new', 'contacted', 'qualified', 'rejected');--> statement-breakpoint
CREATE TABLE "app"."leads" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"company_id" uuid NOT NULL,
	"person_id" uuid,
	"research_job_id" uuid,
	"score" smallint,
	"score_breakdown" jsonb,
	"status" "app"."lead_status" DEFAULT 'new' NOT NULL,
	"assignee_user_id" uuid,
	"overrides" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"custom_fields" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"contacted_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "leads_score_range" CHECK ("app"."leads"."score" is null or "app"."leads"."score" between 0 and 100)
);
--> statement-breakpoint
ALTER TABLE "app"."leads" ADD CONSTRAINT "leads_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."leads" ADD CONSTRAINT "leads_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "app"."companies"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."leads" ADD CONSTRAINT "leads_workspace_org_fk" FOREIGN KEY ("workspace_id","org_id") REFERENCES "app"."workspaces"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."leads" ADD CONSTRAINT "leads_job_org_fk" FOREIGN KEY ("research_job_id","org_id") REFERENCES "app"."research_jobs"("id","org_id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "leads_workspace_company_person_key" ON "app"."leads" USING btree ("workspace_id","company_id",coalesce("person_id", '00000000-0000-0000-0000-000000000000'::uuid));--> statement-breakpoint
CREATE INDEX "leads_workspace_score_idx" ON "app"."leads" USING btree ("workspace_id","score" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "leads_workspace_status_idx" ON "app"."leads" USING btree ("workspace_id","status");--> statement-breakpoint
CREATE INDEX "leads_job_idx" ON "app"."leads" USING btree ("research_job_id","created_at" DESC NULLS LAST);