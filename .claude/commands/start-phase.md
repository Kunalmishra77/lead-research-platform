---
description: Load context and produce an implementation plan for a phase
argument-hint: <phase-number>
---
We are starting Phase $ARGUMENTS.

1. Read `CLAUDE.md`, the latest 3 entries of `PROGRESS.md`, and `phases/PHASES.md`.
2. Confirm every phase listed as a dependency is marked done. If not, stop and tell me what is missing.
3. Open the phase file for this number in `phases/` (files are zero-padded, e.g. phase 1 = `phases/phase-01-foundation.md`) and read every doc listed under "Read before starting".
4. Inspect the current repo state relevant to this phase (do not assume).
5. Produce a plan with:
   - Your understanding of the phase goal in 3-5 bullets
   - Ordered task list (map to the phase checklist), each with files to create/change and tests to write
   - New dependencies with the exact reason (verify current stable versions)
   - Risks, open questions, and any ADRs needed
6. Do NOT write code yet. Wait for my approval of the plan.
7. After approval, set the phase status to `in progress` in `phases/PHASES.md`.
