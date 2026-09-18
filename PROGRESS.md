# PROGRESS.md — build log

Append a new entry at the TOP after every working session. Keep entries short. Claude reads the latest 3 entries at the start of each session.

## Template

```
### YYYY-MM-DD — Phase N — <short title>
- Done:
- Decisions (link ADRs):
- Tests/checks status:
- Open issues / blockers:
- Next step:
```

---

### 2026-09-18 — Phase 1 — Task 1.4 contracts package
- Done: `packages/contracts` with JSON Schemas (job-envelope v1, progress-event v1, research-spec v1 skeleton), `scripts/gen.ts` (TS types + schema consts via json-schema-to-typescript; Pydantic v2 models via datamodel-codegen 0.82 with `WireModel` base + `to_wire()`), ajv 2020 validators, shared fixtures, `pnpm contracts:gen` / `contracts:check` (also removes/flags orphaned generated files). Base tsconfig moved to `packages/config/tsconfig/base.json` (Vite does not follow pnpm symlinks for relative `extends`).
- Decisions (link ADRs): envelope budget uses `cost_cap_micros` (integer money) instead of `cost_cap_usd`; progress event carries `org_id` + `trace_id`; envelope if/then requires `org_id` when `research_job_id` is set (TS-only in schema; workers parser must re-check in 1.9); Python strict int/str/bool; `fields` uniqueness enforced TS-side only.
- Tests/checks status: vitest 32 passed (fixtures + Python->TS cross-language round trip), pytest 23 passed / 1 skipped (TS-only if/then rule); typecheck, lint, format, contracts:check green. Code-reviewer: CHANGES REQUESTED -> all blocker/should-fix items addressed.
- Open issues / blockers: CI must install uv + Python 3.12 for the contracts job (task 1.14); `ajvFormats.default` interop may need a fallback if validators are ever bundled for the browser.
- Next step: task 1.3 hosted infra setup (portable Redis, DoH resolver for supabase.co, bootstrap SQL) with in-memory credentials.

### 2026-09-18 — Phase 1 — Task 1.2 monorepo tooling
- Done: pnpm workspace + Turborepo 2.10, `tsconfig.base.json` (strict, noUncheckedIndexedAccess), `packages/config` (ESLint 10 flat configs `base` + type-aware `typescript(dir)`, tsconfig presets node/next/library), Prettier, EditorConfig, `.gitattributes` (LF), Husky pre-commit (lint-staged) + commit-msg (commitlint), `.nvmrc` 24.
- Decisions (link ADRs): ADR-0004 versions; import order via `eslint-plugin-simple-import-sort` (eslint-plugin-import does not support ESLint 10); pnpm 11 auto-added `minimumReleaseAgeExclude: prettier@3.9.8`.
- Tests/checks status: `pnpm lint`, `pnpm typecheck` (no TS packages yet), `pnpm format:check` pass; lint probe catches unsorted imports + unused vars; commitlint rejects "bad msg", accepts "chore: ok".
- Open issues / blockers: Node is 22.18 locally (engines warns, wants 24); owner actions from ADR-0002 still pending.
- Next step: task 1.3 hosted infra setup (needs Redis Cloud URL, test project, DoH, `.env`).

### 2026-09-18 — Phase 1 — Phase started, plan approved
- Done: Phase 1 plan approved; phase set to in progress; task 1.1 ticked (ADR-0002/0003); ADR-0004 pins TS 6.0, NestJS 11 + nestjs-zod, Node 24, asyncpg.
- Decisions (link ADRs): docs/adr/0004-toolchain-version-pins.md; platform staff flag set manually via SQL for now.
- Tests/checks status: n/a (no code yet).
- Open issues / blockers: owner to narrow the `.env.*` deny rule in `.claude/settings.json` (Claude is blocked from self-editing settings) so `.env.example` can be maintained; owner actions from ADR-0002 still pending (Redis Cloud, test project, Node 24, Windows DoH, `.env`).
- Next step: task 1.2 monorepo tooling.

### 2026-09-18 — Phase 0 — Orientation, hosting and auth decisions
- Done: orientation review of all docs; git repo initialised with GitHub remote; ADR-0002 (no Docker: Supabase DB/Auth/Storage + Redis Cloud) and ADR-0003 (DB roles, `app` schema, bootstrap/admin functions, partitioning) accepted; docs, CLAUDE.md and phase-01 updated to match.
- Decisions (link ADRs): docs/adr/0002-hosted-dev-services-and-supabase-auth.md, docs/adr/0003-db-roles-schema-and-partitioning.md. Node 24 LTS target; Python 3.12 via uv.
- Tests/checks status: Supabase dev project verified: PG 17.6, all needed extensions available, SET LOCAL works through transaction pooler, JWKS serves ES256 key.
- Open issues / blockers: ISP DNS-blocks `*.supabase.co` on the dev machine (works over DoH) -> enable Windows DoH; `.env` must be created by the owner (Claude denied); Redis Cloud DB and `leadforge-test` project not yet created; DB password + S3 key were shared in chat -> rotate; Google Places ToS review for the shared graph/exports still to start.
- Next step: `/start-phase 1`.

### (no entries yet) — Phase 0 — Repository initialised with planning files
- Done: planning docs, phase files, Claude Code commands added
- Next step: run FIRST-PROMPT.md in Claude Code to plan Phase 1
