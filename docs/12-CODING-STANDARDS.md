# 12 — Coding Standards

## General
- Small PR-sized changes; one concern per commit; Conventional Commits (`feat(api): ...`, `fix(workers): ...`).
- No dead code, no commented-out blocks, no TODO without an issue reference in PROGRESS.md.
- Files ~300 lines max; functions ~50 lines max.
- Names say what, not how. No abbreviations except well-known (id, url, api).
- Configuration via typed config modules; no `process.env` / `os.environ` outside config.

## TypeScript
- `strict: true`, `noUncheckedIndexedAccess: true`; no `any` (use `unknown` + narrowing).
- Zod schemas are the source of request/response types; OpenAPI generated from them.
- NestJS: module per feature (controller thin, service holds logic, repository for DB).
- Drizzle: queries in repositories only; always run tenant queries through `withTenant(orgId, tx => ...)`.
- React: function components, hooks; server components for static shells; client components for interactive grids.
- ESLint + Prettier; import order enforced.

## Python
- Type hints everywhere; `mypy --strict` on `app/`; ruff (lint + format).
- Async I/O only in workers; no blocking calls in the event loop (use `asyncio.to_thread` for CPU-light sync libs).
- Pydantic models for all envelopes, connector raw/mapped data, AI outputs.
- Pure functions for mapping/normalization/scoring (easy to test).
- SQLAlchemy Core with explicit columns; no `select *` in hot paths.
- Every job handler: validate envelope -> check idempotency -> check budget -> do work -> record usage -> emit progress -> ack. Exceptions mapped to the error taxonomy.

## Data rules
- Every write of an observed value creates a `field_values` row with full provenance.
- AI outputs flagged `method=ai` with `model` and `prompt_version`.
- Units and formats: UTC timestamps, E.164 phones, ISO country codes, integer money (minor units), credits as integers.

## Errors and logging
- Throw typed errors (`AppError` with `code`, `class`, `httpStatus`) — map in one exception filter (API) / job runner (workers).
- Structured logs; include `trace_id`, `org_id`, `job_id`; mask secrets and personal data in logs.

## Security in code
- Parameterized queries only. No raw SQL string concatenation.
- Never log or return secrets. Encrypt credentials with the crypto module.
- Validate and cap sizes of all external input (uploads, bodies, fetched pages).

## Git and CI
- Branch per task: `phase-<n>/<short-task>`.
- CI: install -> lint -> typecheck -> unit tests -> integration tests (Supabase CLI stack + Redis service container) -> contracts generation check (no diff) -> build.
