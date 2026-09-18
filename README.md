# LeadForge (codename)

Multi-source lead generation and deep research SaaS. See `CLAUDE.md` for how this repo is built and `phases/PHASES.md` for the roadmap.

## Quick start

Needs Node 24, pnpm 11, uv (Python 3.12 is installed by uv) and the hosted services described in `infra/setup/SETUP.md` (Supabase project; no Docker).

```bash
pnpm install
cp infra/setup/env.template .env        # fill in values, see infra/setup/SETUP.md
pnpm redis:start                        # separate terminal, keep running
pnpm infra:bootstrap && pnpm infra:buckets && pnpm infra:check   # every line PASS
pnpm db:migrate && pnpm db:seed
pnpm dev                                # builds workspace packages, then web :3000 + API :4000/docs
cd services/workers && uv sync && uv run python -m app.main      # separate terminal
```

Sign up at http://localhost:3000/signup, confirm the email, create your organization, then open **Job pipeline check** in the sidebar to see a `system.ping` job run end to end.

Checks: `pnpm lint && pnpm typecheck && pnpm test`, `pnpm --filter @leadforge/web e2e`, and in `services/workers`: `uv run ruff check . && uv run mypy && uv run pytest`. The full list is in `CLAUDE.md` (Commands). CI: `.github/workflows/ci.yml`.

## Documentation

- Product: `docs/00-PRODUCT-BRIEF.md`
- Architecture: `docs/01-ARCHITECTURE.md`
- Roadmap: `phases/PHASES.md`
- Decisions: `docs/adr/`
- Build log: `PROGRESS.md`
