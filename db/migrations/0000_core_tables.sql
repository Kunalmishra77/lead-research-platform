CREATE TYPE "app"."credit_reason" AS ENUM('grant', 'purchase', 'subscription_renewal', 'reserve', 'consume', 'release', 'refund', 'expire', 'adjustment');--> statement-breakpoint
CREATE TYPE "app"."error_class" AS ENUM('transient', 'rate_limited', 'access_restricted', 'parse_failed', 'invalid_input', 'budget_exhausted');--> statement-breakpoint
CREATE TYPE "app"."membership_role" AS ENUM('owner', 'admin', 'manager', 'member', 'viewer');--> statement-breakpoint
CREATE TYPE "app"."research_depth" AS ENUM('quick', 'standard', 'deep');--> statement-breakpoint
CREATE TYPE "app"."research_job_status" AS ENUM('queued', 'planning', 'running', 'paused', 'completed', 'failed', 'cancelled');--> statement-breakpoint
CREATE TYPE "app"."source_type" AS ENUM('api', 'crawl', 'registry', 'provider', 'user');--> statement-breakpoint
CREATE TYPE "app"."tos_class" AS ENUM('green', 'amber', 'red');--> statement-breakpoint
CREATE TABLE "app"."memberships" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"role" "app"."membership_role" NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "memberships_workspace_user_key" UNIQUE("workspace_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "app"."organizations" (
	"id" uuid PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"slug" text NOT NULL,
	"plan" text DEFAULT 'free' NOT NULL,
	"region" text DEFAULT 'IN' NOT NULL,
	"credits_balance" bigint DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "organizations_slug_key" UNIQUE("slug"),
	CONSTRAINT "organizations_slug_format" CHECK ("app"."organizations"."slug" ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'),
	CONSTRAINT "organizations_name_length" CHECK (char_length("app"."organizations"."name") between 1 and 120),
	CONSTRAINT "organizations_region_iso2" CHECK ("app"."organizations"."region" ~ '^[A-Z]{2}$')
);
--> statement-breakpoint
CREATE TABLE "app"."user_profiles" (
	"user_id" uuid PRIMARY KEY NOT NULL,
	"full_name" text,
	"is_platform_staff" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "app"."workspaces" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"name" text NOT NULL,
	"settings" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "workspaces_name_length" CHECK (char_length("app"."workspaces"."name") between 1 and 120)
);
--> statement-breakpoint
CREATE TABLE "app"."sources" (
	"id" uuid PRIMARY KEY NOT NULL,
	"key" text NOT NULL,
	"name" text NOT NULL,
	"type" "app"."source_type" NOT NULL,
	"tos_class" "app"."tos_class" NOT NULL,
	"legal_approved" boolean DEFAULT false NOT NULL,
	"reliability" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"cost_per_call_micros" bigint DEFAULT 0 NOT NULL,
	"default_ttl_days" integer DEFAULT 30 NOT NULL,
	"enabled" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "sources_key_key" UNIQUE("key"),
	CONSTRAINT "sources_key_format" CHECK ("app"."sources"."key" ~ '^[a-z][a-z0-9_]{1,63}$'),
	CONSTRAINT "sources_red_needs_legal" CHECK (not ("app"."sources"."tos_class" = 'red' and "app"."sources"."enabled" and not "app"."sources"."legal_approved"))
);
--> statement-breakpoint
CREATE TABLE "app"."research_jobs" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"search_id" uuid,
	"created_by" uuid,
	"status" "app"."research_job_status" DEFAULT 'queued' NOT NULL,
	"depth" "app"."research_depth" DEFAULT 'standard' NOT NULL,
	"credit_budget" integer DEFAULT 0 NOT NULL,
	"credits_reserved" integer DEFAULT 0 NOT NULL,
	"credits_used" integer DEFAULT 0 NOT NULL,
	"cost_micros" bigint DEFAULT 0 NOT NULL,
	"progress" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"error_class" "app"."error_class",
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "research_jobs_credits_nonnegative" CHECK ("app"."research_jobs"."credit_budget" >= 0 and "app"."research_jobs"."credits_reserved" >= 0 and "app"."research_jobs"."credits_used" >= 0 and "app"."research_jobs"."cost_micros" >= 0)
);
--> statement-breakpoint
CREATE TABLE "app"."credit_ledger" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"delta" bigint NOT NULL,
	"balance_after" bigint NOT NULL,
	"reason" "app"."credit_reason" NOT NULL,
	"ref_type" text,
	"ref_id" uuid,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "credit_ledger_delta_nonzero" CHECK ("app"."credit_ledger"."delta" <> 0)
);
--> statement-breakpoint
CREATE TABLE "app"."usage_unit_keys" (
	"org_id" uuid NOT NULL,
	"research_job_id" uuid NOT NULL,
	"meter" text NOT NULL,
	"unit_key" text NOT NULL,
	"usage_event_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "usage_unit_keys_pkey" PRIMARY KEY("research_job_id","meter","unit_key")
);
--> statement-breakpoint
ALTER TABLE "app"."memberships" ADD CONSTRAINT "memberships_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."memberships" ADD CONSTRAINT "memberships_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "app"."workspaces"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."workspaces" ADD CONSTRAINT "workspaces_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD CONSTRAINT "research_jobs_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD CONSTRAINT "research_jobs_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "app"."workspaces"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."credit_ledger" ADD CONSTRAINT "credit_ledger_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" ADD CONSTRAINT "usage_unit_keys_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."usage_unit_keys" ADD CONSTRAINT "usage_unit_keys_research_job_id_research_jobs_id_fk" FOREIGN KEY ("research_job_id") REFERENCES "app"."research_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "memberships_user_id_idx" ON "app"."memberships" USING btree ("user_id");--> statement-breakpoint
CREATE INDEX "memberships_org_id_idx" ON "app"."memberships" USING btree ("org_id");--> statement-breakpoint
CREATE INDEX "workspaces_org_id_idx" ON "app"."workspaces" USING btree ("org_id");--> statement-breakpoint
CREATE INDEX "research_jobs_org_status_idx" ON "app"."research_jobs" USING btree ("org_id","status");--> statement-breakpoint
CREATE INDEX "research_jobs_workspace_created_idx" ON "app"."research_jobs" USING btree ("workspace_id","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "credit_ledger_org_created_idx" ON "app"."credit_ledger" USING btree ("org_id","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "usage_unit_keys_org_idx" ON "app"."usage_unit_keys" USING btree ("org_id");