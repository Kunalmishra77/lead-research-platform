CREATE TYPE "app"."job_run_status" AS ENUM('queued', 'running', 'completed', 'failed', 'cancelled');--> statement-breakpoint
CREATE TABLE "app"."job_runs" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"type" text NOT NULL,
	"status" "app"."job_run_status" DEFAULT 'queued' NOT NULL,
	"attempts" integer DEFAULT 0 NOT NULL,
	"input" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"result" jsonb,
	"error_class" "app"."error_class",
	"error" text,
	"created_by" uuid,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "job_runs_type_format" CHECK ("app"."job_runs"."type" ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
	CONSTRAINT "job_runs_attempts_range" CHECK ("app"."job_runs"."attempts" between 0 and 5)
);
--> statement-breakpoint
ALTER TABLE "app"."job_runs" ADD CONSTRAINT "job_runs_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."job_runs" ADD CONSTRAINT "job_runs_workspace_org_fk" FOREIGN KEY ("workspace_id","org_id") REFERENCES "app"."workspaces"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "job_runs_workspace_created_idx" ON "app"."job_runs" USING btree ("workspace_id","created_at" DESC NULLS LAST);