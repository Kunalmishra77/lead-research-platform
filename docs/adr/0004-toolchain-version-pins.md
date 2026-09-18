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
