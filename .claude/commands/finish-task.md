---
description: Verify, record and commit the current task
---
1. Run all relevant checks: `pnpm lint`, `pnpm typecheck`, `pnpm test` for touched TS packages; `uv run ruff check . && uv run mypy . && uv run pytest` for workers if touched.
2. Fix failures. Never disable a test or lint rule to make it pass without telling me.
3. Tick the task checkbox in the phase file.
4. Update `CLAUDE.md` Commands section if any command changed.
5. Add a new top entry to `PROGRESS.md` using its template.
6. Show me `git diff --stat` and propose a Conventional Commit message; commit after I confirm.
