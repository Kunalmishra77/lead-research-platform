CREATE TABLE "app"."credit_rates" (
	"meter" text PRIMARY KEY NOT NULL,
	"credits_per_unit" integer NOT NULL,
	"unit" text NOT NULL,
	"description" text NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "credit_rates_meter_format" CHECK ("app"."credit_rates"."meter" ~ '^[a-z][a-z0-9_]{1,63}$'),
	CONSTRAINT "credit_rates_nonnegative" CHECK ("app"."credit_rates"."credits_per_unit" >= 0)
);
--> statement-breakpoint
CREATE TABLE "app"."plans" (
	"plan" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"signup_credits" integer DEFAULT 0 NOT NULL,
	"monthly_credits" integer DEFAULT 0 NOT NULL,
	"seats" integer DEFAULT 1 NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "plans_nonnegative" CHECK ("app"."plans"."signup_credits" >= 0 and "app"."plans"."monthly_credits" >= 0 and "app"."plans"."seats" >= 1)
);
--> statement-breakpoint
ALTER TABLE "app"."research_jobs" ADD COLUMN "settled_at" timestamp with time zone;