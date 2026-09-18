-- Composite (id, org_id) keys so tenant rows can never reference another org's rows.
-- Reordered by hand: the UNIQUE targets must exist before the composite FKs that reference them.
ALTER TABLE "app"."workspaces" ADD CONSTRAINT "workspaces_id_org_key" UNIQUE("id","org_id");--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD CONSTRAINT "research_jobs_id_org_key" UNIQUE("id","org_id");--> statement-breakpoint
ALTER TABLE "app"."memberships" DROP CONSTRAINT "memberships_workspace_id_workspaces_id_fk";--> statement-breakpoint
ALTER TABLE "app"."research_jobs" DROP CONSTRAINT "research_jobs_workspace_id_workspaces_id_fk";--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" DROP CONSTRAINT "usage_unit_keys_research_job_id_research_jobs_id_fk";--> statement-breakpoint
DROP INDEX "app"."usage_unit_keys_org_idx";--> statement-breakpoint
ALTER TABLE "app"."memberships" ADD CONSTRAINT "memberships_workspace_org_fk" FOREIGN KEY ("workspace_id","org_id") REFERENCES "app"."workspaces"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD CONSTRAINT "research_jobs_workspace_org_fk" FOREIGN KEY ("workspace_id","org_id") REFERENCES "app"."workspaces"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" ADD CONSTRAINT "usage_unit_keys_job_org_fk" FOREIGN KEY ("research_job_id","org_id") REFERENCES "app"."research_jobs"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" DROP CONSTRAINT "usage_unit_keys_pkey";--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" ADD CONSTRAINT "usage_unit_keys_pkey" PRIMARY KEY("org_id","research_job_id","meter","unit_key");
