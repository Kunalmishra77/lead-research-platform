-- LeadForge database bootstrap (ADR-0002, ADR-0003). Idempotent.
-- Run as the Supabase `postgres` role through DATABASE_URL_MIGRATIONS (session pooler) via
-- `pnpm infra:bootstrap`, which also sets the app role passwords. Drizzle migrations assume this ran.

create schema if not exists app;
create schema if not exists extensions;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists citext with schema extensions;
create extension if not exists pg_trgm with schema extensions;
create extension if not exists vector with schema extensions;
create extension if not exists postgis with schema extensions;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_api') then
    create role app_api login noinherit nobypassrls nocreatedb nocreaterole;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_worker') then
    create role app_worker login noinherit nobypassrls nocreatedb nocreaterole;
  end if;
end
$$;

-- Schema `app` is never exposed through the Supabase Data API roles.
revoke all on schema app from public;
revoke all on schema app from anon, authenticated;
grant usage on schema app to app_api, app_worker;
grant usage on schema extensions to app_api, app_worker;

-- Tables (owned by postgres, created by migrations) are usable by the app roles; RLS decides rows.
-- Every app table must have RLS enabled + forced; the RLS test suite (task 1.8) asserts it.
alter default privileges for role postgres in schema app
  grant select, insert, update, delete on tables to app_api, app_worker;
alter default privileges for role postgres in schema app
  grant usage, select on sequences to app_api, app_worker;
-- No blanket "grant ... on all tables": migrations narrow privileges table by table (append-only
-- tables, column grants, partition children), and a re-run of this script must never undo that.

-- Functions are not callable unless a migration grants EXECUTE explicitly (ADR-0003).
-- Per-schema default privileges cannot remove the global PUBLIC grant, so this must be global:
-- it applies to functions `postgres` creates from now on (we do not use the Supabase Data API).
alter default privileges for role postgres revoke execute on functions from public;
revoke execute on all functions in schema app from public;

alter role app_api set search_path = app, extensions;
alter role app_worker set search_path = app, extensions;
alter role app_api set statement_timeout = '15s';
alter role app_worker set statement_timeout = '60s';
