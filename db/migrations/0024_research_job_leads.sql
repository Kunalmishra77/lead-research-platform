CREATE TABLE "app"."research_job_leads" (
	"org_id" uuid NOT NULL,
	"research_job_id" uuid NOT NULL,
	"lead_id" uuid NOT NULL,
	"is_new" boolean NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "research_job_leads_pkey" PRIMARY KEY("research_job_id","lead_id")
);
--> statement-breakpoint
ALTER TABLE "app"."research_job_leads" ADD CONSTRAINT "research_job_leads_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_job_leads" ADD CONSTRAINT "research_job_leads_lead_id_leads_id_fk" FOREIGN KEY ("lead_id") REFERENCES "app"."leads"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_job_leads" ADD CONSTRAINT "research_job_leads_job_org_fk" FOREIGN KEY ("research_job_id","org_id") REFERENCES "app"."research_jobs"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "research_job_leads_lead_idx" ON "app"."research_job_leads" USING btree ("lead_id");