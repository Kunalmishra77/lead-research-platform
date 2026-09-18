# PHASES.md — Roadmap and status

Status values: `not started` · `in progress` · `done (YYYY-MM-DD)`. Only one phase `in progress` at a time.
MVP (paid beta) = Phases 1-7. Effort in person-weeks (pw) assumes a 4-5 person team; with Claude Code doing most implementation, calendar time can shrink, but acceptance criteria do not.

| # | Phase | Depends on | Effort | Status | File |
| --- | --- | --- | --- | --- | --- |
| 1 | Foundation | - | 10 pw | not started | phase-01-foundation.md |
| 2 | Search & research jobs | 1 | 10 pw | not started | phase-02-search.md |
| 3 | Crawling & extraction | 2 | 14 pw | not started | phase-03-extraction.md |
| 4 | Lead database, resolution, verification | 3 | 12 pw | not started | phase-04-lead-database.md |
| 5 | Enrichment | 4 | 10 pw | not started | phase-05-enrichment.md |
| 6 | AI research & scoring | 4, 5 | 12 pw | not started | phase-06-ai-research.md |
| 7 | Export, Sheets, billing -> paid beta | 4 | 8 pw | not started | phase-07-export-billing.md |
| 8 | Automation, signals, public API | 6, 7 | 10 pw | not started | phase-08-automation-api.md |
| 9 | CRM integrations | 7, 8 | 12 pw | not started | phase-09-crm.md |
| 10 | Scaling & reliability | 8 | 14 pw | not started | phase-10-scaling.md |
| 11 | Commercial SaaS launch | all | 20 pw | not started | phase-11-commercial.md |

## Working rhythm per phase
1. `/start-phase <n>` -> approve plan
2. Loop: `/next-task` -> implement -> `code-reviewer` agent -> `/finish-task`
3. `/review-phase <n>` -> mark done
