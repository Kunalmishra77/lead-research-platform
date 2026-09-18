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

### 2026-09-18 — Phase 0 — Orientation, hosting and auth decisions
- Done: orientation review of all docs; git repo initialised with GitHub remote; ADR-0002 (no Docker: Supabase DB/Auth/Storage + Redis Cloud) and ADR-0003 (DB roles, `app` schema, bootstrap/admin functions, partitioning) accepted; docs, CLAUDE.md and phase-01 updated to match.
- Decisions (link ADRs): docs/adr/0002-hosted-dev-services-and-supabase-auth.md, docs/adr/0003-db-roles-schema-and-partitioning.md. Node 24 LTS target; Python 3.12 via uv.
- Tests/checks status: Supabase dev project verified: PG 17.6, all needed extensions available, SET LOCAL works through transaction pooler, JWKS serves ES256 key.
- Open issues / blockers: ISP DNS-blocks `*.supabase.co` on the dev machine (works over DoH) -> enable Windows DoH; `.env` must be created by the owner (Claude denied); Redis Cloud DB and `leadforge-test` project not yet created; DB password + S3 key were shared in chat -> rotate; Google Places ToS review for the shared graph/exports still to start.
- Next step: `/start-phase 1`.

### (no entries yet) — Phase 0 — Repository initialised with planning files
- Done: planning docs, phase files, Claude Code commands added
- Next step: run FIRST-PROMPT.md in Claude Code to plan Phase 1
