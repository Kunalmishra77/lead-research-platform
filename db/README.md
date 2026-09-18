# @leadforge/db

Drizzle schema, migrations and tenant helpers. Decisions: `docs/adr/0003-db-roles-schema-and-partitioning.md`.

## Rules

- **Never use `drizzle-kit push` or `drizzle-kit introspect`/`pull` against a shared database.** RLS
  policies, grants, definer functions, `auth.users` FKs and the partitioned tables live in custom SQL
  migrations that Drizzle does not model; push/pull would drop or miss them.
- Schema change: edit `schema/*.ts`, run `pnpm db:generate`, review the SQL (drizzle-kit can order
  statements wrongly, e.g. an FK before the UNIQUE it references), then `pnpm db:migrate`.
- Security change (policies, grants, functions): `pnpm --filter @leadforge/db generate:custom --name=<what>`
  and write SQL by hand. Every new `app` table needs RLS enabled + forced and policies; `test/structure.test.ts`
  fails otherwise.
- Never edit a migration that has been applied anywhere shared; add a new one.
- Partitioned tables (`schema/partitioned.ts`) are created in SQL and excluded from `drizzle.config.ts`.
- Tenant queries only inside `withTenant(db, { orgId, userId }, tx => ...)`. With a `userId`, membership
  is re-checked in the database (`NotAMemberError`). Pre-org user queries use `withUser`.

## Commands

```
pnpm db:generate     # SQL migration from schema/*.ts
pnpm db:migrate      # apply as owner (DATABASE_URL_MIGRATIONS)
pnpm db:seed         # idempotent seeds
pnpm --filter @leadforge/db test   # structural security tests run when DATABASE_URL_MIGRATIONS / DATABASE_URL are set
```
