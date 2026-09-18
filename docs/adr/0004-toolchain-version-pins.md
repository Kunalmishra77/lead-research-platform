# ADR-0004: Toolchain version pins for Phase 1

- Status: Accepted
- Date: 2026-09-18

## Context
docs/02 says "install the latest stable version". On 2026-09-18 the latest majors of two core tools are not supported by libraries we depend on:
- TypeScript latest is 7.0.2, but `typescript-eslint` 8.70 declares `typescript >=4.8.4 <6.1.0`.
- NestJS latest is 12.0.3, but `nestjs-zod` 5.5 declares `@nestjs/common ^10 || ^11` and `@nestjs/swagger` up to ^11.

The Python DB driver also needs a choice on Windows: psycopg's async mode needs a selector event loop there, while Playwright (Phase 3) needs the default proactor loop.

## Options considered
1. Latest majors (TS 7, Nest 12) with our own Zod pipe and OpenAPI wiring, and no type-aware linting until typescript-eslint catches up: more custom code, weaker linting.
2. Pin to the newest versions the ecosystem supports (TS 6.0.x, Nest 11.2.x + nestjs-zod): standard setup, still receiving patches.

## Decision
Option 2.
- TypeScript `~6.0.3`.
- `@nestjs/*` `^11.2.5` (Fastify adapter) with `nestjs-zod` 5.x and `@nestjs/swagger` 11.x.
- Node 24 LTS (`engines` + `.nvmrc`).
- Python workers use `asyncpg` (via SQLAlchemy async) with the statement cache disabled for the Supabase transaction pooler.

## Consequences
- Revisit when typescript-eslint supports TS 7 and nestjs-zod supports Nest 12: bump in one PR with a note here.
- asyncpg works with the Windows default event loop, so Playwright can share the process model in Phase 3.

## Amendment 2026-09-18 (task 1.14): patched Fastify, trusted proxies
- `@nestjs/platform-fastify` 11.2.5 (latest 11.x) pins `fastify` 5.11.3, which has GHSA-w2qp-rph6-63g4 (schema validation bypass) and GHSA-3m5p-2c4r-xxw2 (X-Forwarded-* spoofing with hop-count `trustProxy`). A pnpm override in `pnpm-workspace.yaml` forces `fastify` 5.12.5 (same major). Remove the override when the adapter ships a patched pin.
- Fastify 5.12 dropped hop-count `trustProxy`. `TRUST_PROXY` now accepts only explicit proxy IPs/CIDRs; `true` and hop counts are refused at startup, because a spoofed `request.ip` would end up in audit rows.
- CI (`.github/workflows/ci.yml`) pins action majors and tool versions (Supabase CLI 2.117.0, gitleaks 8.30.1 with a checksum, pip-audit 2.10.0). The dependency audit fails on `high` and above. Known moderate finding: esbuild's dev-server advisory (GHSA-67mh-4wv8-2f99), reached only through drizzle-kit dev tooling and never shipped.
