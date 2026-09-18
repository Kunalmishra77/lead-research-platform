# LeadForge (codename)

Multi-source lead generation and deep research SaaS. See `CLAUDE.md` for how this repo is built and `phases/PHASES.md` for the roadmap.

## Quick start (after Phase 1)

```bash
cp .env.example .env   # fill in Supabase + Redis Cloud values (see infra/setup/)
pnpm install
pnpm db:migrate && pnpm db:seed
pnpm dev
cd services/workers && uv sync && uv run python -m app.main
```

## Documentation

- Product: `docs/00-PRODUCT-BRIEF.md`
- Architecture: `docs/01-ARCHITECTURE.md`
- Roadmap: `phases/PHASES.md`
- Decisions: `docs/adr/`
- Build log: `PROGRESS.md`
