CREATE TYPE "app"."entity_type" AS ENUM('company', 'person', 'location');--> statement-breakpoint
CREATE TYPE "app"."geo_kind" AS ENUM('country', 'state', 'district', 'city', 'locality');--> statement-breakpoint
CREATE TYPE "app"."research_task_status" AS ENUM('queued', 'running', 'completed', 'failed', 'skipped', 'cancelled');--> statement-breakpoint
CREATE TYPE "app"."value_method" AS ENUM('api', 'crawl', 'ai', 'user', 'provider');--> statement-breakpoint
CREATE TABLE "app"."companies" (
	"id" uuid PRIMARY KEY NOT NULL,
	"canonical_name" text NOT NULL,
	"normalized_name" text NOT NULL,
	"primary_domain" text,
	"country" char(2),
	"state" text,
	"city" text,
	"industry_id" uuid,
	"company_status" text,
	"best" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"confidence" real,
	"merged_into_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "companies_primary_domain_key" UNIQUE("primary_domain"),
	CONSTRAINT "companies_domain_format" CHECK ("app"."companies"."primary_domain" ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$' and "app"."companies"."primary_domain" not like 'www.%'),
	CONSTRAINT "companies_country_format" CHECK ("app"."companies"."country" ~ '^[A-Z]{2}$'),
	CONSTRAINT "companies_confidence_range" CHECK ("app"."companies"."confidence" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "app"."company_domains" (
	"id" uuid PRIMARY KEY NOT NULL,
	"company_id" uuid NOT NULL,
	"domain" text NOT NULL,
	"is_primary" boolean DEFAULT false NOT NULL,
	"is_platform" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "company_domains_company_domain_key" UNIQUE("company_id","domain"),
	CONSTRAINT "company_domains_domain_format" CHECK ("app"."company_domains"."domain" ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$' and "app"."company_domains"."domain" not like 'www.%')
);
--> statement-breakpoint
CREATE TABLE "app"."company_locations" (
	"id" uuid PRIMARY KEY NOT NULL,
	"company_id" uuid NOT NULL,
	"address" text,
	"city" text,
	"state" text,
	"postal_code" text,
	"country" char(2),
	"lat" double precision,
	"lng" double precision,
	"google_place_id" text,
	"phone_e164" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "company_locations_place_key" UNIQUE("google_place_id"),
	CONSTRAINT "company_locations_phone_e164" CHECK ("app"."company_locations"."phone_e164" ~ '^[+][1-9][0-9]{6,14}$'),
	CONSTRAINT "company_locations_country_format" CHECK ("app"."company_locations"."country" ~ '^[A-Z]{2}$'),
	CONSTRAINT "company_locations_latlng" CHECK (("app"."company_locations"."lat" is null) = ("app"."company_locations"."lng" is null) and ("app"."company_locations"."lat" is null or ("app"."company_locations"."lat" between -90 and 90 and "app"."company_locations"."lng" between -180 and 180)))
);
--> statement-breakpoint
CREATE TABLE "app"."geo_areas" (
	"id" uuid PRIMARY KEY NOT NULL,
	"parent_id" uuid,
	"kind" "app"."geo_kind" NOT NULL,
	"country" char(2) NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"aliases" text[] DEFAULT '{}'::text[] NOT NULL,
	"min_lat" double precision NOT NULL,
	"min_lng" double precision NOT NULL,
	"max_lat" double precision NOT NULL,
	"max_lng" double precision NOT NULL,
	"population" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "geo_areas_country_format" CHECK ("app"."geo_areas"."country" ~ '^[A-Z]{2}$'),
	CONSTRAINT "geo_areas_slug_format" CHECK ("app"."geo_areas"."slug" ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
	CONSTRAINT "geo_areas_bbox_valid" CHECK ("app"."geo_areas"."min_lat" between -90 and 90 and "app"."geo_areas"."max_lat" between -90 and 90
          and "app"."geo_areas"."min_lng" between -180 and 180 and "app"."geo_areas"."max_lng" between -180 and 180
          and "app"."geo_areas"."min_lat" < "app"."geo_areas"."max_lat" and "app"."geo_areas"."min_lng" < "app"."geo_areas"."max_lng")
);
--> statement-breakpoint
CREATE TABLE "app"."industries" (
	"id" uuid PRIMARY KEY NOT NULL,
	"parent_id" uuid,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"naics_code" text,
	"synonyms" text[] DEFAULT '{}'::text[] NOT NULL,
	"google_types" text[] DEFAULT '{}'::text[] NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "industries_slug_format" CHECK ("app"."industries"."slug" ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
);
--> statement-breakpoint
CREATE TABLE "app"."research_tasks" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"research_job_id" uuid NOT NULL,
	"parent_task_id" uuid,
	"type" text NOT NULL,
	"input" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"status" "app"."research_task_status" DEFAULT 'queued' NOT NULL,
	"attempts" smallint DEFAULT 0 NOT NULL,
	"credit_budget" integer DEFAULT 0 NOT NULL,
	"cost_micros" bigint DEFAULT 0 NOT NULL,
	"output" jsonb,
	"error_class" "app"."error_class",
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "research_tasks_id_org_key" UNIQUE("id","org_id"),
	CONSTRAINT "research_tasks_id_job_key" UNIQUE("id","research_job_id"),
	CONSTRAINT "research_tasks_not_own_parent" CHECK ("app"."research_tasks"."parent_task_id" <> "app"."research_tasks"."id"),
	CONSTRAINT "research_tasks_type_format" CHECK ("app"."research_tasks"."type" ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
	CONSTRAINT "research_tasks_attempts_range" CHECK ("app"."research_tasks"."attempts" between 0 and 5),
	CONSTRAINT "research_tasks_nonnegative" CHECK ("app"."research_tasks"."credit_budget" >= 0 and "app"."research_tasks"."cost_micros" >= 0)
);
--> statement-breakpoint
CREATE TABLE "app"."searches" (
	"id" uuid PRIMARY KEY NOT NULL,
	"org_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"user_id" uuid,
	"raw_query" text NOT NULL,
	"spec" jsonb NOT NULL,
	"spec_version" smallint NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "searches_id_org_key" UNIQUE("id","org_id"),
	CONSTRAINT "searches_raw_query_length" CHECK (length("app"."searches"."raw_query") between 1 and 2000),
	CONSTRAINT "searches_spec_version_positive" CHECK ("app"."searches"."spec_version" >= 1)
);
--> statement-breakpoint
ALTER TABLE "app"."companies" ADD CONSTRAINT "companies_industry_id_industries_id_fk" FOREIGN KEY ("industry_id") REFERENCES "app"."industries"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."companies" ADD CONSTRAINT "companies_merged_into_fk" FOREIGN KEY ("merged_into_id") REFERENCES "app"."companies"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."company_domains" ADD CONSTRAINT "company_domains_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "app"."companies"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."company_locations" ADD CONSTRAINT "company_locations_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "app"."companies"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."geo_areas" ADD CONSTRAINT "geo_areas_parent_fk" FOREIGN KEY ("parent_id") REFERENCES "app"."geo_areas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."industries" ADD CONSTRAINT "industries_parent_fk" FOREIGN KEY ("parent_id") REFERENCES "app"."industries"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_tasks" ADD CONSTRAINT "research_tasks_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_tasks" ADD CONSTRAINT "research_tasks_job_org_fk" FOREIGN KEY ("research_job_id","org_id") REFERENCES "app"."research_jobs"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."research_tasks" ADD CONSTRAINT "research_tasks_parent_job_fk" FOREIGN KEY ("parent_task_id","research_job_id") REFERENCES "app"."research_tasks"("id","research_job_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."searches" ADD CONSTRAINT "searches_org_id_organizations_id_fk" FOREIGN KEY ("org_id") REFERENCES "app"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "app"."searches" ADD CONSTRAINT "searches_workspace_org_fk" FOREIGN KEY ("workspace_id","org_id") REFERENCES "app"."workspaces"("id","org_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "companies_geo_industry_idx" ON "app"."companies" USING btree ("country","city","industry_id");--> statement-breakpoint
CREATE UNIQUE INDEX "company_domains_domain_key" ON "app"."company_domains" USING btree ("domain") WHERE not "app"."company_domains"."is_platform";--> statement-breakpoint
CREATE INDEX "company_locations_company_idx" ON "app"."company_locations" USING btree ("company_id");--> statement-breakpoint
CREATE UNIQUE INDEX "geo_areas_country_kind_slug_key" ON "app"."geo_areas" USING btree ("country","kind","slug");--> statement-breakpoint
CREATE INDEX "geo_areas_parent_idx" ON "app"."geo_areas" USING btree ("parent_id");--> statement-breakpoint
CREATE UNIQUE INDEX "industries_slug_key" ON "app"."industries" USING btree ("slug");--> statement-breakpoint
CREATE INDEX "research_tasks_parent_idx" ON "app"."research_tasks" USING btree ("parent_task_id");--> statement-breakpoint
CREATE INDEX "research_tasks_job_status_idx" ON "app"."research_tasks" USING btree ("research_job_id","status");--> statement-breakpoint
CREATE INDEX "searches_workspace_created_idx" ON "app"."searches" USING btree ("workspace_id","created_at" DESC NULLS LAST);--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD CONSTRAINT "research_jobs_search_org_fk" FOREIGN KEY ("search_id","org_id") REFERENCES "app"."searches"("id","org_id") ON DELETE no action ON UPDATE no action;